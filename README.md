# Saham Recommender

Skrip otomatis untuk mengambil data saham LQ45 Indonesia, menganalisis valuasi fundamental, dan mengirimkan rekomendasi **Top 10 saham undervalued** via Gmail setiap hari kerja — dijalankan otomatis via **GitHub Actions**.

## Cara Kerja

1. Ambil harga saham dari **IDX API resmi** (fallback ke yfinance)
2. Ambil data finansial dari **Yahoo Finance / yfinance** (P/E, P/B, ROE, Debt/Equity)
3. Hitung **composite score** — saham dengan valuasi murah & fundamental bagus mendapat skor tertinggi
4. Kirim email HTML berisi **Top 10 saham** via **Gmail API (OAuth)**

### Metrik & Bobot Scoring

| Metrik | Bobot | Interpretasi |
|--------|-------|--------------|
| P/E Ratio | 35% | Rendah = undervalued |
| P/B Ratio | 30% | Rendah = di bawah nilai buku |
| ROE | 20% | Tinggi = profitabilitas bagus |
| Debt/Equity | 15% | Rendah = utang sehat |

### Cara Perhitungan Score

**Step 1 — Filter data tidak valid**

Saham dibuang sebelum scoring jika:
- P/E atau P/B ≤ 0 (perusahaan rugi)
- P/E > 200 atau P/B > 50 (outlier / data error)
- Ada data yang kosong atau harga = 0

**Step 2 — Rank Score per Metrik (skala 0–100)**

Setiap metrik diubah menjadi skor 0–100 berdasarkan **ranking relatif** antar saham yang lolos filter, bukan nilai absolutnya:

```
rank_score = (rank / jumlah_saham) × 100
```

| Metrik | Aturan | Contoh (dari 25 saham) |
|--------|--------|------------------------|
| P/E | Rendah = bagus → rank tertinggi = skor 100 | P/E 3.0x → rank 25 → skor ~100 |
| P/B | Rendah = bagus → rank tertinggi = skor 100 | P/B 0.12x → rank 25 → skor ~100 |
| ROE | Tinggi = bagus → rank tertinggi = skor 100 | ROE 23.4% → rank tertinggi |
| D/E | Rendah = bagus → rank tertinggi = skor 100 | D/E 0.04x → skor ~96 |

> Menggunakan ranking (bukan nilai absolut) karena P/E dan D/E punya skala berbeda — ranking membuat semua metrik sebanding di skala 0–100.

**Step 3 — Composite Score**

```
Score = (PE_score × 35%) + (PB_score × 30%) + (ROE_score × 20%) + (DE_score × 15%)
```

Contoh perhitungan EMTK (score 74.6):

| Metrik | Nilai | Skor | × Bobot | Kontribusi |
|--------|-------|------|---------|-----------|
| P/E | 6.25x | 80 | × 35% | 28.0 |
| P/B | 1.24x | 64 | × 30% | 19.2 |
| ROE | 19.1% | 88 | × 20% | 17.6 |
| D/E | 0.04x | 96 | × 15% | 14.4 |
| **Total** | | | | **79.2** |

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

Tambahkan 4 secrets berikut:

| Secret Name | Nilai |
|-------------|-------|
| `GMAIL_TOKEN_JSON` | Isi file `token.json` (copy-paste seluruh isinya) |
| `GMAIL_CREDENTIALS_JSON` | Isi file `credentials.json` (copy-paste seluruh isinya) |
| `SENDER_EMAIL` | Email Gmail yang di-authorize (pengirim) |
| `RECIPIENT_EMAIL` | Email tujuan penerima rekomendasi |

---

### 3. Aktifkan GitHub Actions

GitHub Actions workflow sudah ada di `.github/workflows/daily-recommender.yml`.

Jadwal: **setiap hari kerja pukul 08:30 WIB** (01:30 UTC).

Untuk test manual: buka tab **Actions** di GitHub → pilih workflow → klik **Run workflow**.

---

## Jalankan Lokal

```bash
pip install -r requirements.txt
python setup_gmail.py   # sekali saja
python main.py
```

File `preview.html` akan dibuat sebagai preview email.

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
| `Token OAuth tidak valid` | Jalankan ulang `python setup_gmail.py`, update secret `GMAIL_TOKEN_JSON` |
| Data saham kosong/sedikit | Normal jika market tutup; yfinance bisa rate-limit |
| Email tidak terkirim | Cek Actions log; pastikan email ada di OAuth consent test users |
| GitHub Actions tidak jalan | Pastikan Actions diaktifkan di Settings repo |

---

## Catatan Penting

- **LQ45 diperbarui** setiap Februari & Agustus oleh IDX. Update list `LQ45_SYMBOLS` di `main.py` sesuai konstituen terbaru.
- Data finansial bersumber dari Yahoo Finance dan bisa ada keterlambatan 1 hari.
- Skrip ini hanya untuk **referensi riset**, bukan saran investasi resmi.
