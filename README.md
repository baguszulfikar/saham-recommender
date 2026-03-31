# Saham Recommender

Skrip otomatis untuk mengambil data saham LQ45 Indonesia, menganalisis valuasi fundamental dengan **7 metrik**, dan mengirimkan rekomendasi **Top 10 saham undervalued** via Gmail setiap hari kerja — dijalankan otomatis via **GitHub Actions**.

## Cara Kerja

1. Ambil harga saham dari **IDX API resmi** (fallback ke yfinance)
2. Ambil data finansial dari **Yahoo Finance / yfinance** (7 metrik fundamental)
3. Filter saham yang berpotensi **value trap** sebelum scoring
4. Hitung **composite score** dengan bobot berbeda untuk bank vs non-bank
5. Kirim email HTML berisi **Top 10 saham** via **Gmail API (OAuth)**

---

## Metodologi Scoring (v2)

### Metrik & Bobot

Berdasarkan riset mendalam tentang analisis fundamental saham IDX, versi ini menggunakan **7 metrik** dengan bobot berbeda untuk **bank** dan **non-bank**.

| Metrik | Non-Bank | Bank | Interpretasi |
|--------|----------|------|--------------|
| P/E Ratio | 20% | 20% | Rendah = undervalued relatif terhadap laba |
| P/B Ratio | 15% | 25% | Rendah = di bawah nilai buku; bobot lebih tinggi untuk bank |
| ROE | 20% | 30% | Tinggi = profitabilitas bagus; metrik utama bank |
| D/E Ratio | 10% | — | Rendah = utang sehat; **tidak relevan untuk bank** |
| EV/EBITDA | 15% | — | Rendah = undervalued; lebih robust dari P/E karena netral struktur modal |
| FCF Yield | 10% | 10% | Tinggi = arus kas bebas kuat relatif market cap |
| Revenue Growth | 10% | 15% | Tinggi = pertumbuhan pendapatan; bobot lebih tinggi untuk bank |

> **Mengapa bank berbeda?** Bank memiliki leverage by nature — D/E tinggi adalah hal wajar, bukan risiko. EV/EBITDA juga tidak bermakna untuk bank. ROE adalah metrik utama efisiensi bank.

### Value Trap Guard

Sebelum scoring, saham yang memenuhi kriteria berikut **dibuang** untuk menghindari jebakan value trap (saham tampak murah tapi fundamental lemah):

| Filter | Threshold | Alasan |
|--------|-----------|--------|
| ROE minimum | < 8% | ROE rendah + harga murah = classic value trap |
| Current Ratio (non-bank) | < 0.8 | Risiko likuiditas jangka pendek |
| Revenue Growth | < -15% YoY | Pendapatan anjlok = sinyal fundamental memburuk |
| P/E outlier | > 200x | Data error atau laba hampir nol |
| P/B outlier | > 50x | Data error |

### Cara Perhitungan Score

**Step 1 — Filter data tidak valid & value trap guard** (lihat tabel di atas)

**Step 2 — Rank Score per Metrik (skala 0–100)**

Setiap metrik diubah menjadi skor 0–100 berdasarkan **ranking relatif** antar saham yang lolos filter:

```
rank_score = (rank / jumlah_saham) × 100
```

| Metrik | Aturan | Contoh |
|--------|--------|--------|
| P/E, P/B, D/E, EV/EBITDA | Rendah = bagus → rank tertinggi = skor 100 | P/E 5x di antara yang terkecil → skor ~100 |
| ROE, FCF Yield, Rev Growth | Tinggi = bagus → rank tertinggi = skor 100 | ROE 23% tertinggi → skor ~100 |

> Menggunakan ranking (bukan nilai absolut) karena P/E dan EV/EBITDA punya skala berbeda. Ranking membuat semua metrik sebanding di skala 0–100.

**Step 3 — Composite Score**

```
# Non-bank
Score = (PE×20%) + (PB×15%) + (ROE×20%) + (DE×10%) + (EV_EBITDA×15%) + (FCF_Yield×10%) + (Rev_Growth×10%)

# Bank
Score = (PE×20%) + (PB×25%) + (ROE×30%) + (FCF_Yield×10%) + (Rev_Growth×15%)
```

### Benchmark Valuasi Per Sektor IDX

| Sektor | P/E Wajar | P/B Wajar | ROE Sehat | Catatan |
|--------|-----------|-----------|-----------|---------|
| Perbankan | 12–17x | 1–3x | >15% | Gunakan NIM, NPL, CAR untuk analisis mendalam |
| Properti | 10–20x | 0.5–1.5x | 5–15% | Discount to NAV lebih tepat dari P/E |
| Consumer/FMCG | 18–30x | 2–5x | >20% | P/E tinggi wajar jika ROE dan growth tinggi |
| Mining | Tidak reliable | 0.8–2x | — | Pakai EV/EBITDA normalized 5–7 tahun |
| Infrastruktur/Telco | 15–22x | — | — | FCF Yield & EV/EBITDA lebih relevan |

---

## Setup

### 1. Setup Gmail API (SEKALI, dari lokal)

#### a. Buat Google Cloud Project & Enable Gmail API

1. Buka [Google Cloud Console](https://console.cloud.google.com/)
2. Buat project baru → beri nama misal `SahamRecommender`
3. Klik **APIs & Services** → **Library** → cari `Gmail API` → **Enable**

#### b. Buat OAuth Credentials

1. Klik **APIs & Services** → **Credentials** → **+ Create Credentials** → **OAuth client ID**
2. Konfigurasi **OAuth consent screen** jika diminta:
   - User Type: **External**, App name: `Saham Recommender`
   - Di tab **Scopes**, tambahkan: `https://www.googleapis.com/auth/gmail.send`
   - Di tab **Test users**, tambahkan email Gmail Anda
3. Application type: **Desktop app** → **Create**
4. Download JSON → simpan sebagai `credentials.json` di folder ini

#### c. Generate Token (sekali saja)

```bash
pip install -r requirements.txt
python setup_gmail.py
```

Browser terbuka → login Gmail → `token.json` tersimpan otomatis.

---

### 2. Setup GitHub Actions Secrets

Buka repo ini di GitHub → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**

| Secret Name | Nilai |
|-------------|-------|
| `GMAIL_TOKEN_JSON` | Isi file `token.json` (copy-paste seluruh isinya) |
| `GMAIL_CREDENTIALS_JSON` | Isi file `credentials.json` (copy-paste seluruh isinya) |
| `SENDER_EMAIL` | Email Gmail yang di-authorize (pengirim) |
| `RECIPIENT_EMAIL` | Email tujuan, bisa beberapa dipisah koma: `a@gmail.com,b@gmail.com` |

---

### 3. Aktifkan GitHub Actions

Workflow sudah ada di `.github/workflows/daily-recommender.yml`.

Jadwal: **setiap hari kerja pukul 08:30 WIB** (01:30 UTC).

Untuk test manual: tab **Actions** → **Daily Saham Recommender** → **Run workflow**.

---

## Jalankan Lokal

```bash
pip install -r requirements.txt
python setup_gmail.py   # sekali saja
python main.py
```

File `preview.html` dibuat otomatis sebagai preview email.

---

## Struktur File

```
saham-recommender/
├── .github/
│   └── workflows/
│       └── daily-recommender.yml  # GitHub Actions schedule
├── main.py                        # Script utama
├── setup_gmail.py                 # Setup Gmail OAuth (jalankan lokal, sekali)
├── requirements.txt               # Python dependencies
├── credentials.json               # (buat sendiri — jangan di-commit)
├── token.json                     # (auto-generated — jangan di-commit)
└── preview.html                   # (auto-generated setiap run)
```

---

## Troubleshooting

| Problem | Solusi |
|---------|--------|
| `credentials.json not found` | Download dari Google Cloud Console (lihat step 1b) |
| `invalid_scope` error | Pastikan scope `gmail.send` ditambahkan di OAuth consent screen, lalu re-generate token |
| `Token OAuth tidak valid` | Jalankan ulang `python setup_gmail.py`, update secret `GMAIL_TOKEN_JSON` |
| Data saham kosong/sedikit | Normal jika market tutup; yfinance bisa rate-limit |
| Email tidak terkirim | Cek Actions log; pastikan email ada di OAuth consent test users |
| Saham lolos filter sedikit | Normal — value trap guard memfilter saham ROE rendah atau revenue anjlok |

---

## Referensi Riset

Metodologi scoring didasarkan pada referensi berikut:

- [IDX Summary Financial Ratio by Industry](https://idx.co.id/id/data-pasar/laporan-statistik/digital-statistic/monthly/financial-report-and-ratio-of-listed-companies/summary-financial-ratio-by-industry) — Benchmark rasio keuangan per sektor BEI
- [IFG Progress: Eco Bulletin on Trend Analyses of IDX 2024](https://ifgprogress.id/wp-content/uploads/2024/03/Eco._Bulletin_on_Trend_Analyses_of_IDX_.pdf) — Analisis tren pasar modal Indonesia
- [Indonesia P/E Ratio Historical — CEIC Data](https://www.ceicdata.com/en/indicator/indonesia/pe-ratio) — Historis P/E pasar IDX sejak 1992
- [Implementation of Lo Kheng Hong Investment Strategy — EJBMR](https://eu-opensci.org/index.php/ejbmr/article/view/52089) — Studi kuantitatif strategi value investing di IDX
- [Value Investor Lo Kheng Hong: How I Choose Stocks — Jakarta Globe](https://jakartaglobe.id/business/value-investor-lo-kheng-hong-how-i-choose-the-right-stocks) — Kriteria seleksi saham Lo Kheng Hong
- [Fokus PBV & P/E? Hati-hati Value Trap — Investing.com](https://id.investing.com/analysis/fokus-pada-pbv-dan-pe-hatihati-terjebak-value-trap-200249215) — Analisis jebakan value trap di IDX
- [Top Free Cash Flow Yield Stocks — Quant Investing](https://www.quant-investing.com/blog/top-free-cash-flow-yield-stocks-for-2025) — Studi 40 tahun: FCF Yield sebagai prediktor return terbaik
- [EV/EBITDA vs P/E — The Footnotes Analyst](https://www.footnotesanalyst.com/relative-valuation-conflicts-ev-ebitda-versus-p-e/) — Perbandingan keunggulan EV/EBITDA vs P/E
- [Indonesia Banks Sector Analysis — Sectors.app](https://sectors.app/indonesia/banks) — Data dan analisis sektor perbankan IDX
- [Indonesia Stock Market Valuation — GuruFocus](https://www.gurufocus.com/global-market-valuation.php?country=IDN) — Buffett Indicator & overall market valuation IDX

---

## Catatan Penting

- **LQ45 diperbarui** setiap Februari & Agustus oleh IDX. Update list `LQ45_SYMBOLS` di `main.py` sesuai konstituen terbaru.
- Data finansial bersumber dari Yahoo Finance dan bisa ada keterlambatan 1 hari.
- **Bank symbols** (`BBCA`, `BBNI`, `BBRI`, `BBTN`, `BMRI`, `BRIS`, `BTPS`) mendapat bobot scoring berbeda — update `BANK_SYMBOLS` di `main.py` jika konstituen bank dalam LQ45 berubah.
- Skrip ini hanya untuk **referensi riset**, bukan saran investasi resmi.
