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
RECIPIENT_LIST = [e.strip() for e in RECIPIENT_EMAIL.split(",") if e.strip()]

TOKEN_FILE = os.path.join(os.path.dirname(__file__), "token.json")
CREDENTIALS_FILE = os.path.join(os.path.dirname(__file__), "credentials.json")

# ---------------------------------------------------------------------------
# Bobot scoring — berbeda untuk bank vs non-bank
#
# Referensi riset:
#   - FCF Yield: 40-year study, top predictor of returns (Quant Investing)
#   - EV/EBITDA: lebih robust dari P/E untuk perusahaan dengan leverage berbeda
#   - D/E tidak relevan untuk bank (leverage by nature); ROE lebih representatif
#   - Sumber: IFG Progress IDX 2024, IDX Summary Financial Ratio by Industry
# ---------------------------------------------------------------------------

WEIGHTS_NONBANK = {
    "pe_score":             0.20,
    "pb_score":             0.15,
    "roe_score":            0.20,
    "de_score":             0.10,
    "ev_ebitda_score":      0.15,
    "fcf_yield_score":      0.10,
    "revenue_growth_score": 0.10,
}

WEIGHTS_BANK = {
    "pe_score":             0.20,
    "pb_score":             0.25,  # P/B sangat relevan untuk bank
    "roe_score":            0.30,  # ROE adalah metrik utama bank
    "de_score":             0.00,  # tidak relevan untuk bank
    "ev_ebitda_score":      0.00,  # tidak relevan untuk bank
    "fcf_yield_score":      0.10,
    "revenue_growth_score": 0.15,
}

# Simbol bank dalam LQ45 — D/E dan EV/EBITDA di-skip untuk kelompok ini
BANK_SYMBOLS = {"BBCA", "BBNI", "BBRI", "BBTN", "BMRI", "BRIS", "BTPS"}

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
# 1. Ambil harga dari IDX API (fallback ke yfinance)
# ---------------------------------------------------------------------------

def fetch_idx_prices() -> dict:
    url = "https://www.idx.co.id/primary/StockData/GetAllStockSnapshot"
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; SahamRecommender/1.0)",
        "Referer": "https://www.idx.co.id/",
    }
    try:
        resp = requests.get(url, headers=headers, timeout=30)
        resp.raise_for_status()
        summaries = resp.json().get("data", {}).get("summaries", [])
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
# 2. Ambil data finansial dari yfinance
#
# Metrik baru vs versi sebelumnya:
#   + EV/EBITDA       : lebih robust dari P/E, netral terhadap struktur modal
#   + FCF Yield       : Free Cash Flow / Market Cap, prediktor return jangka panjang
#   + Revenue Growth  : pertumbuhan pendapatan YoY, filter value trap
#   + Current Ratio   : likuiditas jangka pendek, filter risiko kebangkrutan
# ---------------------------------------------------------------------------

def fetch_financial_data(symbols: list) -> pd.DataFrame:
    idx_prices = fetch_idx_prices()
    records = []

    for symbol in symbols:
        ticker_code = f"{symbol}.JK"
        log.info(f"Mengambil data: {ticker_code}")
        try:
            ticker = yf.Ticker(ticker_code)
            info = ticker.info

            # Harga: utamakan IDX API, fallback ke yfinance
            if symbol in idx_prices and idx_prices[symbol]["close"] > 0:
                close_price = idx_prices[symbol]["close"]
                volume = idx_prices[symbol].get("volume", 0)
                change_pct = idx_prices[symbol].get("change_pct", 0)
            else:
                close_price = info.get("currentPrice") or info.get("regularMarketPrice") or 0
                volume = info.get("regularMarketVolume", 0)
                change_pct = info.get("regularMarketChangePercent", 0)

            # Metrik dasar
            pe_ratio        = info.get("trailingPE") or info.get("forwardPE")
            pb_ratio        = info.get("priceToBook")
            roe             = info.get("returnOnEquity")       # desimal: 0.18 = 18%
            debt_to_equity  = info.get("debtToEquity")         # dalam %, 50.0 = D/E 0.5
            market_cap      = info.get("marketCap", 0)
            sector          = info.get("sector", "")
            company_name    = info.get("longName") or info.get("shortName", symbol)

            dividend_yield  = info.get("dividendYield") or 0
            if dividend_yield > 1:                              # normalisasi jika sudah dalam %
                dividend_yield = dividend_yield / 100

            # Metrik baru
            ev_ebitda       = info.get("enterpriseToEbitda")   # EV/EBITDA ratio
            free_cash_flow  = info.get("freeCashflow")         # absolut (IDR)
            revenue_growth  = info.get("revenueGrowth")        # YoY desimal: 0.05 = 5%
            current_ratio   = info.get("currentRatio")

            # Normalisasi
            roe_pct     = roe * 100 if roe is not None else None
            de_ratio    = debt_to_equity / 100 if debt_to_equity is not None else None
            rev_growth  = revenue_growth * 100 if revenue_growth is not None else None  # dalam %

            # FCF Yield = FCF / Market Cap (dalam %)
            if free_cash_flow and market_cap and market_cap > 0:
                fcf_yield_pct = (free_cash_flow / market_cap) * 100
            else:
                fcf_yield_pct = None

            records.append({
                "symbol":           symbol,
                "company":          company_name,
                "sector":           sector,
                "is_bank":          symbol in BANK_SYMBOLS,
                "price":            close_price,
                "volume":           volume,
                "change_pct":       change_pct,
                "pe_ratio":         pe_ratio,
                "pb_ratio":         pb_ratio,
                "roe_pct":          roe_pct,
                "de_ratio":         de_ratio,
                "ev_ebitda":        ev_ebitda,
                "fcf_yield_pct":    fcf_yield_pct,
                "rev_growth_pct":   rev_growth,
                "current_ratio":    current_ratio,
                "market_cap":       market_cap,
                "dividend_yield_pct": round(dividend_yield * 100, 2) if dividend_yield else 0,
            })
        except Exception as e:
            log.warning(f"Gagal mengambil data {ticker_code}: {e}")
            records.append({
                "symbol": symbol, "company": symbol, "sector": "",
                "is_bank": symbol in BANK_SYMBOLS,
                "price": 0, "volume": 0, "change_pct": 0,
                "pe_ratio": None, "pb_ratio": None, "roe_pct": None,
                "de_ratio": None, "ev_ebitda": None, "fcf_yield_pct": None,
                "rev_growth_pct": None, "current_ratio": None,
                "market_cap": 0, "dividend_yield_pct": 0,
            })

    return pd.DataFrame(records)


# ---------------------------------------------------------------------------
# 3. Scoring & ranking
#
# Metodologi (berbasis riset):
#   - Rank-based scoring (0-100) agar semua metrik sebanding skalanya
#   - Scoring terpisah: bank vs non-bank (bobot & filter berbeda)
#   - Value trap guard: filter saham yang terlihat murah tapi fundamental lemah
# ---------------------------------------------------------------------------

def score_stocks(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    # --- Filter outlier & data tidak valid ---
    # P/E dan P/B: hapus yang negatif, nol, atau outlier ekstrem
    df = df[
        df["pe_ratio"].notna() & (df["pe_ratio"] > 0) & (df["pe_ratio"] < 200) &
        df["pb_ratio"].notna() & (df["pb_ratio"] > 0) & (df["pb_ratio"] < 50)  &
        df["roe_pct"].notna()  & (df["price"] > 0)
    ].copy()

    # --- Value Trap Guard ---
    # 1. ROE minimum: bank >= 8%, non-bank >= 8%
    #    ROE rendah + harga murah = klasik value trap
    df = df[df["roe_pct"] >= 8].copy()

    # 2. Current Ratio: non-bank harus >= 0.8 (likuiditas minimum)
    #    Bank di-skip karena current ratio tidak relevan untuk lembaga keuangan
    nonbank_mask = ~df["is_bank"]
    cr_available = df["current_ratio"].notna()
    df = df[
        df["is_bank"] |                                          # bank: lewatkan filter CR
        ~cr_available |                                          # data tidak ada: lewatkan
        (nonbank_mask & cr_available & (df["current_ratio"] >= 0.8))
    ].copy()

    # 3. Revenue growth: buang saham yang pendapatannya anjlok > 15% YoY
    rg_available = df["rev_growth_pct"].notna()
    df = df[
        ~rg_available |                                          # data tidak ada: lewatkan
        (df["rev_growth_pct"] >= -15)
    ].copy()

    if df.empty:
        log.error("Tidak ada data valid setelah filter value trap!")
        return df

    log.info(f"Saham lolos semua filter: {len(df)}")

    def rank_score(series, lower_is_better=True):
        """
        Konversi nilai ke skor 0-100 berdasarkan ranking relatif.
        lower_is_better=True  → nilai kecil dapat skor tinggi (P/E, P/B, D/E, EV/EBITDA)
        lower_is_better=False → nilai besar dapat skor tinggi (ROE, FCF Yield, Rev Growth)
        """
        valid = series.notna()
        scores = pd.Series(50.0, index=series.index)  # default 50 jika data tidak ada
        if valid.sum() > 1:
            n = valid.sum()
            ranked = series[valid].rank(ascending=not lower_is_better, method="min")
            scores[valid] = (ranked / n * 100).clip(0, 100)
        return scores

    # --- Hitung skor per metrik ---
    df["pe_score"]             = rank_score(df["pe_ratio"],       lower_is_better=True)
    df["pb_score"]             = rank_score(df["pb_ratio"],       lower_is_better=True)
    df["roe_score"]            = rank_score(df["roe_pct"],        lower_is_better=False)
    df["de_score"]             = rank_score(df["de_ratio"],       lower_is_better=True)
    df["ev_ebitda_score"]      = rank_score(df["ev_ebitda"],      lower_is_better=True)
    df["fcf_yield_score"]      = rank_score(df["fcf_yield_pct"],  lower_is_better=False)
    df["revenue_growth_score"] = rank_score(df["rev_growth_pct"], lower_is_better=False)

    # --- Composite score: bobot berbeda untuk bank vs non-bank ---
    def apply_weights(row):
        w = WEIGHTS_BANK if row["is_bank"] else WEIGHTS_NONBANK
        return sum(row[metric] * weight for metric, weight in w.items())

    df["composite_score"] = df.apply(apply_weights, axis=1)
    df = df.sort_values("composite_score", ascending=False).reset_index(drop=True)
    df["rank"] = df.index + 1

    return df


# ---------------------------------------------------------------------------
# 4. Format email HTML
# ---------------------------------------------------------------------------

def format_currency(val, prefix="Rp "):
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


def generate_top3_analysis(top10: pd.DataFrame) -> str:
    """
    Generate penjelasan dinamis mengapa top 3 saham dianggap undervalued,
    berdasarkan nilai metrik masing-masing dibandingkan benchmark IDX.
    """
    # Benchmark rata-rata pasar IDX (berdasarkan riset IDX Summary Financial Ratio)
    MARKET_PE   = 16.0
    MARKET_PB   = 2.0
    MARKET_ROE  = 12.0

    # Benchmark per sektor (P/E, P/B, ROE minimum sehat)
    SECTOR_BENCH = {
        "Financial Services": {"pe": 14, "pb": 1.5, "roe": 15},
        "Basic Materials":    {"pe": 10, "pb": 1.2, "roe": 10},
        "Consumer Defensive": {"pe": 22, "pb": 3.0, "roe": 18},
        "Consumer Cyclical":  {"pe": 18, "pb": 2.0, "roe": 13},
        "Industrials":        {"pe": 15, "pb": 1.5, "roe": 12},
        "Energy":             {"pe": 10, "pb": 1.2, "roe": 10},
        "Technology":         {"pe": 20, "pb": 2.5, "roe": 15},
        "Communication Services": {"pe": 16, "pb": 2.0, "roe": 12},
        "Utilities":          {"pe": 14, "pb": 1.3, "roe": 10},
        "Real Estate":        {"pe": 14, "pb": 1.0, "roe":  8},
        "Healthcare":         {"pe": 25, "pb": 3.0, "roe": 18},
    }

    medal = ["🥇", "🥈", "🥉"]
    cards = ""

    for i, (_, row) in enumerate(top10.head(3).iterrows()):
        symbol  = row["symbol"]
        company = str(row["company"])[:45]
        sector  = str(row.get("sector", "")) or "N/A"
        is_bank = bool(row["is_bank"])

        bench = SECTOR_BENCH.get(sector, {"pe": MARKET_PE, "pb": MARKET_PB, "roe": MARKET_ROE})

        reasons = []

        # --- P/E ---
        pe = row.get("pe_ratio")
        if pe and not math.isnan(pe):
            disc = ((bench["pe"] - pe) / bench["pe"]) * 100
            if disc > 20:
                reasons.append(
                    f"<li><strong>P/E {pe:.1f}x</strong> — {disc:.0f}% di bawah rata-rata sektor "
                    f"({bench['pe']:.0f}x). Harga murah relatif terhadap laba perusahaan.</li>"
                )

        # --- P/B ---
        pb = row.get("pb_ratio")
        if pb and not math.isnan(pb):
            disc = ((bench["pb"] - pb) / bench["pb"]) * 100
            if disc > 15:
                reasons.append(
                    f"<li><strong>P/B {pb:.2f}x</strong> — diperdagangkan {disc:.0f}% di bawah "
                    f"benchmark sektor ({bench['pb']:.1f}x). Aset bersih perusahaan belum "
                    f"sepenuhnya tercermin di harga pasar.</li>"
                )

        # --- ROE ---
        roe = row.get("roe_pct")
        if roe and not math.isnan(roe):
            if roe >= bench["roe"] * 1.2:
                reasons.append(
                    f"<li><strong>ROE {roe:.1f}%</strong> — {roe/bench['roe']:.1f}x di atas "
                    f"rata-rata sektor ({bench['roe']:.0f}%). Manajemen sangat efisien "
                    f"menghasilkan laba dari ekuitas pemegang saham.</li>"
                )
            elif roe >= bench["roe"]:
                reasons.append(
                    f"<li><strong>ROE {roe:.1f}%</strong> — di atas rata-rata sektor "
                    f"({bench['roe']:.0f}%), menunjukkan profitabilitas yang solid.</li>"
                )

        # --- EV/EBITDA (non-bank) ---
        ev = row.get("ev_ebitda")
        if not is_bank and ev and not math.isnan(ev) and ev > 0:
            if ev < 8:
                reasons.append(
                    f"<li><strong>EV/EBITDA {ev:.1f}x</strong> — di bawah 8x menandakan valuasi "
                    f"murah saat dibandingkan dengan nilai operasional bisnis (enterprise value). "
                    f"Metrik ini tidak terpengaruh struktur utang maupun pajak.</li>"
                )

        # --- FCF Yield ---
        fcf = row.get("fcf_yield_pct")
        if fcf and not math.isnan(fcf) and fcf > 3:
            reasons.append(
                f"<li><strong>FCF Yield {fcf:.1f}%</strong> — arus kas bebas positif dan kuat "
                f"({fcf:.1f}% dari market cap). Laba yang dilaporkan didukung oleh kas nyata, "
                f"bukan hanya angka akuntansi.</li>"
            )

        # --- Revenue Growth ---
        rg = row.get("rev_growth_pct")
        if rg and not math.isnan(rg):
            if rg >= 15:
                reasons.append(
                    f"<li><strong>Revenue Growth {rg:.1f}%</strong> — pertumbuhan pendapatan "
                    f"tinggi mengindikasikan momentum bisnis yang kuat. Valuasi murah + "
                    f"pertumbuhan = peluang menarik.</li>"
                )
            elif rg >= 5:
                reasons.append(
                    f"<li><strong>Revenue Growth {rg:.1f}%</strong> — pertumbuhan pendapatan "
                    f"stabil, memastikan ini bukan value trap akibat bisnis yang menyusut.</li>"
                )

        # --- D/E (non-bank) ---
        de = row.get("de_ratio")
        if not is_bank and de is not None and not math.isnan(de) and de < 0.4:
            reasons.append(
                f"<li><strong>D/E {de:.2f}x</strong> — utang sangat rendah, memberikan ruang "
                f"finansial untuk ekspansi atau bertahan di kondisi ekonomi sulit.</li>"
            )

        # Fallback jika tidak ada alasan spesifik
        if not reasons:
            reasons.append(
                f"<li>Kombinasi P/E, P/B, ROE, dan metrik lainnya menempatkan saham ini "
                f"di antara yang paling undervalued secara composite score di LQ45.</li>"
            )

        cards += f"""
        <div style="border:1px solid #e0e0e0; border-radius:10px; padding:18px 20px;
                    margin-bottom:14px; background:#fff; border-left: 5px solid
                    {'#FFD700' if i==0 else '#C0C0C0' if i==1 else '#CD7F32'};">
          <div style="display:flex; align-items:center; margin-bottom:10px;">
            <span style="font-size:22px; margin-right:10px;">{medal[i]}</span>
            <div>
              <strong style="font-size:16px; color:#1a1a2e;">#{i+1} {symbol}</strong>
              <span style="font-size:12px; color:#888; margin-left:8px;">Score: {fmt(row['composite_score'], 1)}</span><br>
              <span style="font-size:12px; color:#555;">{company}</span>
              <span style="font-size:11px; color:#888; margin-left:6px;">| {sector}</span>
            </div>
          </div>
          <p style="margin:0 0 8px; font-size:12px; color:#555; font-style:italic;">
            Mengapa saham ini undervalued?
          </p>
          <ul style="margin:0; padding-left:18px; font-size:13px; color:#333; line-height:1.8;">
            {''.join(reasons)}
          </ul>
        </div>"""

    return cards


def build_email_html(top10: pd.DataFrame, fetch_date: str) -> str:
    top3_cards = generate_top3_analysis(top10)

    rank_colors = [
        "#FFD700", "#C0C0C0", "#CD7F32",
        "#4CAF50", "#4CAF50", "#4CAF50",
        "#2196F3", "#2196F3", "#2196F3", "#2196F3",
    ]

    rows = ""
    for _, row in top10.iterrows():
        rank = int(row["rank"])
        badge_color = rank_colors[rank - 1] if rank <= len(rank_colors) else "#9E9E9E"
        change_color = "#4CAF50" if row["change_pct"] >= 0 else "#F44336"
        change_sign = "+" if row["change_pct"] >= 0 else ""
        bank_badge = ' <span style="font-size:10px;background:#E3F2FD;color:#1565C0;padding:1px 5px;border-radius:3px;">BANK</span>' if row["is_bank"] else ""

        # FCF Yield: warna hijau jika positif
        fcf_val = row.get("fcf_yield_pct")
        fcf_color = "#4CAF50" if (fcf_val and fcf_val > 0) else "#F44336"

        # Revenue growth: warna hijau jika positif
        rg_val = row.get("rev_growth_pct")
        rg_color = "#4CAF50" if (rg_val and rg_val >= 0) else "#F44336"

        rows += f"""
        <tr style="border-bottom: 1px solid #f0f0f0;">
          <td style="padding: 10px 6px; text-align: center;">
            <span style="background:{badge_color}; color:{'#333' if rank <= 3 else '#fff'};
                         padding: 3px 8px; border-radius: 20px; font-weight: bold; font-size: 13px;">
              #{rank}
            </span>
          </td>
          <td style="padding: 10px 6px;">
            <strong style="font-size:14px; color:#1a1a2e;">{row['symbol']}</strong>{bank_badge}<br>
            <span style="font-size:11px; color:#666;">{str(row['company'])[:38]}</span>
          </td>
          <td style="padding: 10px 6px; font-size:11px; color:#555;">{str(row['sector'])[:22] or '-'}</td>
          <td style="padding: 10px 6px; text-align: right;">
            <strong>{format_currency(row['price'])}</strong><br>
            <span style="color:{change_color}; font-size:11px;">{change_sign}{fmt(row['change_pct'], 2)}%</span>
          </td>
          <td style="padding: 10px 6px; text-align: right; color:#333;">{fmt(row['pe_ratio'], 1)}x</td>
          <td style="padding: 10px 6px; text-align: right; color:#333;">{fmt(row['pb_ratio'], 2)}x</td>
          <td style="padding: 10px 6px; text-align: right; color:#4CAF50;">{fmt(row['roe_pct'], 1)}%</td>
          <td style="padding: 10px 6px; text-align: right; color:#555;">{fmt(row['ev_ebitda'], 1)}x</td>
          <td style="padding: 10px 6px; text-align: right; color:{fcf_color};">{fmt(fcf_val, 1)}%</td>
          <td style="padding: 10px 6px; text-align: right; color:{rg_color};">{fmt(rg_val, 1)}%</td>
          <td style="padding: 10px 6px; text-align: right; font-size:11px; color:#888;">{format_market_cap(row['market_cap'])}</td>
          <td style="padding: 10px 6px; text-align: right;">
            <strong style="color:#1a1a2e;">{fmt(row['composite_score'], 1)}</strong>
          </td>
        </tr>"""

    html = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <style>
    body {{ font-family: 'Segoe UI', Arial, sans-serif; background: #f5f7fa; margin: 0; padding: 20px; }}
    .container {{ max-width: 1000px; margin: 0 auto; background: #fff; border-radius: 12px;
                  box-shadow: 0 4px 20px rgba(0,0,0,0.08); overflow: hidden; }}
    .header {{ background: linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%);
               padding: 28px 36px; color: #fff; }}
    .header h1 {{ margin: 0; font-size: 22px; letter-spacing: 0.5px; }}
    .header p {{ margin: 8px 0 0; opacity: 0.75; font-size: 13px; }}
    .content {{ padding: 24px 16px; }}
    .methodology {{ background: #f8f9ff; border-left: 4px solid #0f3460;
                    padding: 12px 16px; margin-bottom: 20px; border-radius: 0 8px 8px 0;
                    font-size: 12px; color: #444; line-height: 1.6; }}
    .filters {{ background: #fff3e0; border-left: 4px solid #FF9800;
                padding: 10px 16px; margin-bottom: 20px; border-radius: 0 8px 8px 0;
                font-size: 12px; color: #555; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 12px; }}
    thead tr {{ background: #1a1a2e; color: #fff; }}
    thead th {{ padding: 10px 6px; text-align: center; font-weight: 600; font-size: 11px;
                letter-spacing: 0.3px; white-space: nowrap; }}
    tbody tr:hover {{ background: #f8f9ff; }}
    .disclaimer {{ margin-top: 18px; padding: 12px; background: #fff8e1; border-radius: 8px;
                   font-size: 11px; color: #795548; }}
    .footer {{ text-align: center; padding: 18px; font-size: 11px; color: #999;
               border-top: 1px solid #f0f0f0; }}
  </style>
</head>
<body>
<div class="container">
  <div class="header">
    <h1>📊 Top 10 Saham LQ45 Undervalued</h1>
    <p>Rekomendasi harian dengan 7 metrik fundamental &bull; {fetch_date}</p>
  </div>
  <div class="content">
    <div class="methodology">
      <strong>Metodologi Scoring (v2 — berbasis riset fundamental IDX):</strong><br>
      <strong>Non-bank:</strong> P/E (20%) + P/B (15%) + ROE (20%) + D/E (10%) + EV/EBITDA (15%) + FCF Yield (10%) + Revenue Growth (10%)<br>
      <strong>Bank:</strong> P/E (20%) + P/B (25%) + ROE (30%) + FCF Yield (10%) + Revenue Growth (15%)
      &mdash; D/E &amp; EV/EBITDA tidak relevan untuk bank.
    </div>
    <div class="filters">
      <strong>Value Trap Guard aktif:</strong>
      ROE &lt; 8% dibuang &bull; Current Ratio &lt; 0.8 (non-bank) dibuang &bull;
      Revenue Growth &lt; -15% dibuang &bull; P/E &gt; 200x atau P/B &gt; 50x dibuang sebagai outlier.
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
          <th>EV/EBITDA</th>
          <th>FCF Yield</th>
          <th>Rev Growth</th>
          <th>Mkt Cap</th>
          <th>Score</th>
        </tr>
      </thead>
      <tbody>
        {rows}
      </tbody>
    </table>

    <div style="margin-top:28px;">
      <h2 style="font-size:16px; color:#1a1a2e; margin-bottom:14px; padding-bottom:8px;
                  border-bottom:2px solid #f0f0f0;">
        🔍 Analisis Top 3 — Mengapa Saham Ini Undervalued?
      </h2>
      {top3_cards}
    </div>

    <div class="disclaimer">
      ⚠️ <strong>Disclaimer:</strong> Rekomendasi ini dibuat secara otomatis berdasarkan data
      fundamental publik dan bukan merupakan saran investasi resmi. Lakukan riset mandiri
      sebelum mengambil keputusan investasi. Data bersumber dari Yahoo Finance (yfinance) dan IDX API.
    </div>
  </div>
  <div class="footer">
    Saham Recommender v2 &bull; {fetch_date} &bull; Data: yfinance + IDX API
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
    Urutan: env var GMAIL_TOKEN_JSON (GitHub Actions) → file token.json (lokal)
    """
    token_json_str = os.environ.get("GMAIL_TOKEN_JSON")
    if token_json_str:
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
    log.info(f"=== Saham Recommender v2 — {today} ===")

    log.info(f"Mengambil data untuk {len(LQ45_SYMBOLS)} saham LQ45...")
    df = fetch_financial_data(LQ45_SYMBOLS)

    if df.empty:
        log.error("Tidak ada data yang berhasil diambil.")
        return

    log.info(f"Data berhasil diambil: {len(df)} saham")

    scored = score_stocks(df)
    if scored.empty:
        log.error("Scoring gagal — tidak ada saham yang lolos filter.")
        return

    top10 = scored.head(10)
    log.info(
        f"Top 10 saham:\n"
        f"{top10[['rank','symbol','pe_ratio','pb_ratio','roe_pct','ev_ebitda','fcf_yield_pct','rev_growth_pct','composite_score']].to_string(index=False)}"
    )

    fetch_date = datetime.now().strftime("%d %B %Y, %H:%M WIB")
    html = build_email_html(top10, fetch_date)

    preview_path = os.path.join(os.path.dirname(__file__), "preview.html")
    with open(preview_path, "w", encoding="utf-8") as f:
        f.write(html)
    log.info(f"Preview HTML: {preview_path}")

    subject = f"📊 Top 10 Saham LQ45 Undervalued — {datetime.now().strftime('%d %b %Y')}"
    success = send_email(subject, html)

    if success:
        log.info("Selesai! Rekomendasi berhasil dikirim.")
    else:
        log.error("Email gagal dikirim.")


if __name__ == "__main__":
    main()
