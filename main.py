"""
Saham Recommender - Top 10 LQ45 Undervalued Stocks
Kirim rekomendasi via Gmail API setiap hari kerja.

Setup:
  1. Install dependencies: pip install -r requirements.txt
  2. Setup Gmail OAuth: python setup_gmail.py
  3. Jalankan: python main.py
"""

import os
import base64
import json
import logging
import math
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import pandas as pd
import requests
import yfinance as yf
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

# ---------------------------------------------------------------------------
# Konfigurasi
# ---------------------------------------------------------------------------

# Email tujuan — bisa satu atau beberapa, dipisah koma
# Contoh: "a@gmail.com,b@gmail.com,c@gmail.com"
RECIPIENT_EMAIL = os.environ.get("RECIPIENT_EMAIL", "your_email@gmail.com")
SENDER_EMAIL = os.environ.get("SENDER_EMAIL", "your_email@gmail.com")

# Parse jadi list, strip spasi di tiap alamat
RECIPIENT_LIST = [e.strip() for e in RECIPIENT_EMAIL.split(",") if e.strip()]

# File token OAuth
TOKEN_FILE = os.path.join(os.path.dirname(__file__), "token.json")
CREDENTIALS_FILE = os.path.join(os.path.dirname(__file__), "credentials.json")

# Bobot scoring (total harus = 1.0)
# P/E rendah = bagus, P/B rendah = bagus, ROE tinggi = bagus, D/E rendah = bagus
WEIGHTS = {
    "pe_score": 0.35,
    "pb_score": 0.30,
    "roe_score": 0.20,
    "de_score": 0.15,
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# LQ45 — daftar konstituen (update tiap Februari & Agustus dari IDX)
# Sumber: https://www.idx.co.id/en/market-data/statistics/composite-index/lq45/
# ---------------------------------------------------------------------------

LQ45_SYMBOLS = [
    "AALI", "ACES", "ADRO", "AMRT", "ANTM",
    "ASII", "BBCA", "BBNI", "BBRI", "BBTN",
    "BMRI", "BRIS", "BRPT", "BSDE", "BTPS",
    "CPIN", "EMTK", "ERAA", "EXCL", "GGRM",
    "GOTO", "HRUM", "ICBP", "INCO", "INDF",
    "INKP", "INTP", "ISAT", "ITMG", "JPFA",
    "JSMR", "KLBF", "MAPI", "MBMA", "MDKA",
    "MIKA", "MNCN", "PGAS", "PTBA", "PTPP",
    "SMGR", "TLKM", "TOWR", "UNTR", "UNVR",
]

# ---------------------------------------------------------------------------
# 1. Ambil data LQ45 dari IDX API (harga & info dasar)
# ---------------------------------------------------------------------------

def fetch_idx_prices() -> dict:
    """
    Ambil snapshot harga saham dari IDX API resmi.
    Returns dict: {symbol: {"close": float, "volume": int, "change_pct": float}}
    """
    url = "https://www.idx.co.id/primary/StockData/GetAllStockSnapshot"
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; SahamRecommender/1.0)",
        "Referer": "https://www.idx.co.id/",
    }
    try:
        resp = requests.get(url, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        # Struktur respons IDX: {"data": {"summaries": [...]}}
        summaries = data.get("data", {}).get("summaries", [])
        prices = {}
        for item in summaries:
            code = item.get("StockCode", "").strip()
            if code in LQ45_SYMBOLS:
                prices[code] = {
                    "close": float(item.get("ClosingPrice", 0) or 0),
                    "volume": int(item.get("Volume", 0) or 0),
                    "change_pct": float(item.get("PercentChange", 0) or 0),
                }
        log.info(f"IDX API: mendapat harga untuk {len(prices)} saham LQ45")
        return prices
    except Exception as e:
        log.warning(f"IDX API gagal: {e} — harga akan diambil dari yfinance")
        return {}


# ---------------------------------------------------------------------------
# 2. Ambil data finansial dari yfinance (.JK suffix untuk IDX)
# ---------------------------------------------------------------------------

def fetch_financial_data(symbols: list) -> pd.DataFrame:
    """
    Ambil P/E, P/B, ROE, Debt/Equity dari yfinance untuk saham IDX.
    yfinance menggunakan suffix .JK untuk Bursa Efek Indonesia.
    """
    idx_prices = fetch_idx_prices()
    records = []

    for symbol in symbols:
        ticker_code = f"{symbol}.JK"
        log.info(f"Mengambil data: {ticker_code}")
        try:
            ticker = yf.Ticker(ticker_code)
            info = ticker.info

            # Ambil harga: utamakan dari IDX API, fallback ke yfinance
            if symbol in idx_prices and idx_prices[symbol]["close"] > 0:
                close_price = idx_prices[symbol]["close"]
                volume = idx_prices[symbol].get("volume", 0)
                change_pct = idx_prices[symbol].get("change_pct", 0)
            else:
                close_price = info.get("currentPrice") or info.get("regularMarketPrice") or 0
                volume = info.get("regularMarketVolume", 0)
                change_pct = info.get("regularMarketChangePercent", 0)

            pe_ratio = info.get("trailingPE") or info.get("forwardPE")
            pb_ratio = info.get("priceToBook")
            roe = info.get("returnOnEquity")          # desimal, misal 0.18 = 18%
            debt_to_equity = info.get("debtToEquity")  # dalam %, misal 50.0 = D/E 0.5
            market_cap = info.get("marketCap", 0)
            sector = info.get("sector", "")
            company_name = info.get("longName") or info.get("shortName", symbol)
            dividend_yield = info.get("dividendYield") or 0  # desimal (0.05 = 5%)
            # yfinance kadang mengembalikan sudah dalam % (misal 5.0 bukan 0.05) → normalisasi
            if dividend_yield > 1:
                dividend_yield = dividend_yield / 100

            # Normalisasi
            if roe is not None:
                roe_pct = roe * 100
            else:
                roe_pct = None

            if debt_to_equity is not None:
                # yfinance mengembalikan dalam %, bagi 100 untuk rasio
                de_ratio = debt_to_equity / 100
            else:
                de_ratio = None

            records.append({
                "symbol": symbol,
                "company": company_name,
                "sector": sector,
                "price": close_price,
                "volume": volume,
                "change_pct": change_pct,
                "pe_ratio": pe_ratio,
                "pb_ratio": pb_ratio,
                "roe_pct": roe_pct,
                "de_ratio": de_ratio,
                "market_cap": market_cap,
                "dividend_yield_pct": round(dividend_yield * 100, 2) if dividend_yield else 0,
            })
        except Exception as e:
            log.warning(f"Gagal mengambil data {ticker_code}: {e}")
            records.append({
                "symbol": symbol,
                "company": symbol,
                "sector": "",
                "price": 0,
                "volume": 0,
                "change_pct": 0,
                "pe_ratio": None,
                "pb_ratio": None,
                "roe_pct": None,
                "de_ratio": None,
                "market_cap": 0,
                "dividend_yield_pct": 0,
            })

    return pd.DataFrame(records)


# ---------------------------------------------------------------------------
# 3. Scoring & ranking
# ---------------------------------------------------------------------------

def score_stocks(df: pd.DataFrame) -> pd.DataFrame:
    """
    Hitung composite score untuk menemukan saham undervalued.

    Metodologi:
    - P/E score  : semakin rendah P/E, semakin tinggi skor (inverted rank)
    - P/B score  : semakin rendah P/B, semakin tinggi skor (inverted rank)
    - ROE score  : semakin tinggi ROE, semakin tinggi skor
    - D/E score  : semakin rendah D/E, semakin tinggi skor (inverted rank)

    Filter: hapus saham dengan nilai negatif (rugi) atau data tidak lengkap.
    """
    df = df.copy()

    # Filter saham dengan data finansial tidak valid
    df = df[
        df["pe_ratio"].notna() & (df["pe_ratio"] > 0) & (df["pe_ratio"] < 200) &
        df["pb_ratio"].notna() & (df["pb_ratio"] > 0) & (df["pb_ratio"] < 50) &
        df["roe_pct"].notna() & (df["roe_pct"] > 0) &
        df["de_ratio"].notna() & (df["de_ratio"] >= 0) &
        (df["price"] > 0)
    ].copy()

    if df.empty:
        log.error("Tidak ada data valid untuk di-score!")
        return df

    n = len(df)

    def rank_score(series, lower_is_better=True):
        """
        Ubah nilai menjadi skor 0-100 berdasarkan rank.
        lower_is_better=True  → nilai kecil dapat skor tinggi (P/E, P/B, D/E)
        lower_is_better=False → nilai besar dapat skor tinggi (ROE)
        """
        # ascending=False → nilai terbesar dapat rank 1 (terendah), nilai terkecil rank N (tertinggi)
        # Jadi untuk lower_is_better: kita rank ascending=False agar nilai kecil dapat rank N → skor tinggi
        ranked = series.rank(ascending=not lower_is_better, method="min")
        return (ranked / n * 100).clip(0, 100)

    # P/E: rendah = bagus → nilai kecil dapat skor tinggi
    df["pe_score"] = rank_score(df["pe_ratio"], lower_is_better=True)
    # P/B: rendah = bagus → nilai kecil dapat skor tinggi
    df["pb_score"] = rank_score(df["pb_ratio"], lower_is_better=True)
    # ROE: tinggi = bagus → nilai besar dapat skor tinggi
    df["roe_score"] = rank_score(df["roe_pct"], lower_is_better=False)
    # D/E: rendah = bagus → nilai kecil dapat skor tinggi
    df["de_score"] = rank_score(df["de_ratio"], lower_is_better=True)

    # Composite score
    df["composite_score"] = (
        df["pe_score"] * WEIGHTS["pe_score"] +
        df["pb_score"] * WEIGHTS["pb_score"] +
        df["roe_score"] * WEIGHTS["roe_score"] +
        df["de_score"] * WEIGHTS["de_score"]
    )

    df = df.sort_values("composite_score", ascending=False).reset_index(drop=True)
    df["rank"] = df.index + 1

    return df


# ---------------------------------------------------------------------------
# 4. Format email HTML
# ---------------------------------------------------------------------------

def format_currency(val, prefix="Rp "):
    """Format angka ke ribuan dengan titik."""
    if val is None or (isinstance(val, float) and math.isnan(val)):
        return "-"
    return f"{prefix}{val:,.0f}".replace(",", ".")


def format_market_cap(val):
    if not val or val == 0:
        return "-"
    if val >= 1e12:
        return f"Rp {val/1e12:.1f}T"
    if val >= 1e9:
        return f"Rp {val/1e9:.1f}M"
    return f"Rp {val/1e6:.0f}Jt"


def fmt(val, decimals=2, suffix=""):
    if val is None or (isinstance(val, float) and math.isnan(val)):
        return "-"
    return f"{val:.{decimals}f}{suffix}"


def build_email_html(top10: pd.DataFrame, fetch_date: str) -> str:
    # Warna gradient untuk ranking
    rank_colors = [
        "#FFD700", "#C0C0C0", "#CD7F32",  # Gold, Silver, Bronze
        "#4CAF50", "#4CAF50", "#4CAF50",  # Hijau
        "#2196F3", "#2196F3", "#2196F3", "#2196F3",  # Biru
    ]

    rows = ""
    for _, row in top10.iterrows():
        rank = int(row["rank"])
        badge_color = rank_colors[rank - 1] if rank <= len(rank_colors) else "#9E9E9E"
        change_color = "#4CAF50" if row["change_pct"] >= 0 else "#F44336"
        change_sign = "+" if row["change_pct"] >= 0 else ""

        rows += f"""
        <tr style="border-bottom: 1px solid #f0f0f0;">
          <td style="padding: 12px 8px; text-align: center;">
            <span style="background:{badge_color}; color:{'#333' if rank <= 3 else '#fff'};
                         padding: 4px 10px; border-radius: 20px; font-weight: bold; font-size: 14px;">
              #{rank}
            </span>
          </td>
          <td style="padding: 12px 8px;">
            <strong style="font-size:15px; color:#1a1a2e;">{row['symbol']}</strong><br>
            <span style="font-size:12px; color:#666;">{row['company'][:40]}</span>
          </td>
          <td style="padding: 12px 8px; font-size:12px; color:#555;">{row['sector'][:25] or '-'}</td>
          <td style="padding: 12px 8px; text-align: right;">
            <strong>{format_currency(row['price'])}</strong><br>
            <span style="color:{change_color}; font-size:12px;">{change_sign}{fmt(row['change_pct'], 2)}%</span>
          </td>
          <td style="padding: 12px 8px; text-align: right; color:#333;">{fmt(row['pe_ratio'], 1)}x</td>
          <td style="padding: 12px 8px; text-align: right; color:#333;">{fmt(row['pb_ratio'], 2)}x</td>
          <td style="padding: 12px 8px; text-align: right; color:#4CAF50;">{fmt(row['roe_pct'], 1)}%</td>
          <td style="padding: 12px 8px; text-align: right; color:#555;">{fmt(row['de_ratio'], 2)}x</td>
          <td style="padding: 12px 8px; text-align: right; color:#2196F3;">{fmt(row['dividend_yield_pct'], 2)}%</td>
          <td style="padding: 12px 8px; text-align: right; font-size:12px; color:#888;">{format_market_cap(row['market_cap'])}</td>
          <td style="padding: 12px 8px; text-align: right;">
            <strong style="color:#1a1a2e;">{fmt(row['composite_score'], 1)}</strong>
          </td>
        </tr>"""

    html = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <style>
    body {{ font-family: 'Segoe UI', Arial, sans-serif; background: #f5f7fa; margin: 0; padding: 20px; }}
    .container {{ max-width: 900px; margin: 0 auto; background: #fff; border-radius: 12px;
                  box-shadow: 0 4px 20px rgba(0,0,0,0.08); overflow: hidden; }}
    .header {{ background: linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%);
               padding: 30px 40px; color: #fff; }}
    .header h1 {{ margin: 0; font-size: 24px; letter-spacing: 0.5px; }}
    .header p {{ margin: 8px 0 0; opacity: 0.75; font-size: 14px; }}
    .content {{ padding: 30px 20px; }}
    .methodology {{ background: #f8f9ff; border-left: 4px solid #0f3460;
                    padding: 14px 18px; margin-bottom: 25px; border-radius: 0 8px 8px 0;
                    font-size: 13px; color: #444; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
    thead tr {{ background: #1a1a2e; color: #fff; }}
    thead th {{ padding: 12px 8px; text-align: center; font-weight: 600; font-size: 12px;
                letter-spacing: 0.3px; }}
    tbody tr:hover {{ background: #f8f9ff; }}
    .disclaimer {{ margin-top: 20px; padding: 14px; background: #fff8e1; border-radius: 8px;
                   font-size: 12px; color: #795548; }}
    .footer {{ text-align: center; padding: 20px; font-size: 12px; color: #999;
               border-top: 1px solid #f0f0f0; }}
  </style>
</head>
<body>
<div class="container">
  <div class="header">
    <h1>📊 Top 10 Saham LQ45 Undervalued</h1>
    <p>Rekomendasi harian berdasarkan P/E, P/B, ROE, dan Debt/Equity &bull; {fetch_date}</p>
  </div>
  <div class="content">
    <div class="methodology">
      <strong>Metodologi Scoring:</strong>
      P/E Ratio (bobot 35%) + P/B Ratio (30%) + ROE (20%) + Debt/Equity (15%).
      Saham dengan P/E &amp; P/B rendah, ROE tinggi, dan utang rendah mendapat skor tertinggi
      sebagai indikator <em>undervalued</em> secara fundamental.
    </div>
    <table>
      <thead>
        <tr>
          <th>Rank</th>
          <th style="text-align:left;">Saham</th>
          <th style="text-align:left;">Sektor</th>
          <th>Harga</th>
          <th>P/E</th>
          <th>P/B</th>
          <th>ROE</th>
          <th>D/E</th>
          <th>Div. Yield</th>
          <th>Mkt. Cap</th>
          <th>Score</th>
        </tr>
      </thead>
      <tbody>
        {rows}
      </tbody>
    </table>
    <div class="disclaimer">
      ⚠️ <strong>Disclaimer:</strong> Rekomendasi ini dibuat secara otomatis berdasarkan data
      fundamental publik dan bukan merupakan saran investasi resmi. Lakukan riset mandiri
      sebelum mengambil keputusan investasi. Data bersumber dari Yahoo Finance dan IDX.
    </div>
  </div>
  <div class="footer">
    Saham Recommender &bull; Dihasilkan otomatis pada {fetch_date} &bull;
    Data: Yahoo Finance (yfinance) + IDX API
  </div>
</div>
</body>
</html>"""
    return html


# ---------------------------------------------------------------------------
# 5. Kirim email via Gmail API
# ---------------------------------------------------------------------------

def get_gmail_service():
    """
    Buat Gmail API service dengan credentials OAuth.

    Urutan lookup credentials:
    1. Env var GMAIL_TOKEN_JSON (GitHub Actions secrets)
    2. File token.json (lokal)
    """
    # --- Coba dari environment variable (GitHub Actions) ---
    token_json_str = os.environ.get("GMAIL_TOKEN_JSON")
    if token_json_str:
        # Jangan pass scopes= agar tidak trigger validasi ketat;
        # scopes sudah tertanam di dalam token saat setup_gmail.py dijalankan.
        creds = Credentials.from_authorized_user_info(json.loads(token_json_str))
    elif os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE)
    else:
        raise FileNotFoundError(
            "Credentials tidak ditemukan.\n"
            "Lokal: jalankan 'python setup_gmail.py'.\n"
            "GitHub Actions: set secret GMAIL_TOKEN_JSON."
        )

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            # Simpan token yang diperbarui (hanya jika berjalan lokal)
            if not token_json_str and os.path.exists(TOKEN_FILE):
                with open(TOKEN_FILE, "w") as f:
                    f.write(creds.to_json())
        else:
            raise RuntimeError(
                "Token OAuth tidak valid atau expired.\n"
                "Lokal: jalankan 'python setup_gmail.py' untuk login ulang.\n"
                "GitHub Actions: perbarui secret GMAIL_TOKEN_JSON."
            )

    return build("gmail", "v1", credentials=creds)


def send_email(subject: str, html_body: str) -> bool:
    """Kirim email HTML via Gmail API."""
    try:
        service = get_gmail_service()

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = SENDER_EMAIL
        msg["To"] = ", ".join(RECIPIENT_LIST)
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
        service.users().messages().send(userId="me", body={"raw": raw}).execute()
        log.info(f"Email berhasil dikirim ke: {', '.join(RECIPIENT_LIST)}")
        return True
    except Exception as e:
        log.error(f"Gagal mengirim email: {e}")
        return False


# ---------------------------------------------------------------------------
# 6. Main
# ---------------------------------------------------------------------------

def main():
    today = datetime.now().strftime("%A, %d %B %Y")
    log.info(f"=== Saham Recommender — {today} ===")

    # Ambil data finansial semua saham LQ45
    log.info(f"Mengambil data untuk {len(LQ45_SYMBOLS)} saham LQ45...")
    df = fetch_financial_data(LQ45_SYMBOLS)

    if df.empty:
        log.error("Tidak ada data yang berhasil diambil. Proses dihentikan.")
        return

    log.info(f"Data berhasil diambil: {len(df)} saham")

    # Scoring dan ranking
    scored = score_stocks(df)
    if scored.empty:
        log.error("Scoring gagal — tidak ada saham dengan data lengkap.")
        return

    top10 = scored.head(10)
    log.info(f"Top 10 saham terpilih:\n{top10[['rank','symbol','pe_ratio','pb_ratio','roe_pct','de_ratio','composite_score']].to_string(index=False)}")

    # Buat HTML email
    fetch_date = datetime.now().strftime("%d %B %Y, %H:%M WIB")
    html = build_email_html(top10, fetch_date)

    # Simpan HTML untuk preview (opsional)
    preview_path = os.path.join(os.path.dirname(__file__), "preview.html")
    with open(preview_path, "w", encoding="utf-8") as f:
        f.write(html)
    log.info(f"Preview HTML disimpan di: {preview_path}")

    # Kirim email
    subject = f"📊 Top 10 Saham LQ45 Undervalued — {datetime.now().strftime('%d %b %Y')}"
    success = send_email(subject, html)

    if success:
        log.info("Selesai! Rekomendasi berhasil dikirim.")
    else:
        log.error("Email gagal dikirim. Cek log di atas untuk detail.")


if __name__ == "__main__":
    main()
