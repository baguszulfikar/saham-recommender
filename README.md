# Saham Recommender

Skrip otomatis untuk mengambil data saham LQ45 Indonesia, menganalisis valuasi fundamental, dan mengirimkan rekomendasi **Top 10 saham undervalued** via Gmail setiap hari kerja.

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

---

## Setup (Ikuti Urutan Ini)

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Setup Gmail API (SEKALI)

#### a. Buat Google Cloud Project & Enable Gmail API

1. Buka [Google Cloud Console](https://console.cloud.google.com/)
2. Buat project baru: **New Project** → beri nama misal `SahamRecommender`
3. Klik **APIs & Services** → **Library**
4. Cari `Gmail API` → klik **Enable**

#### b. Buat OAuth Credentials

1. Klik **APIs & Services** → **Credentials**
2. Klik **+ Create Credentials** → **OAuth client ID**
3. Jika diminta, konfigurasi **OAuth consent screen**:
   - User Type: **External**
   - App name: `Saham Recommender`
   - Email: email Anda
   - Klik **Save and Continue** (skip bagian lainnya)
   - Di tab **Test users**, tambahkan email Gmail Anda
4. Kembali buat credentials: **Desktop app** → beri nama → **Create**
5. Klik **Download JSON** → simpan sebagai `credentials.json` di folder ini

#### c. Jalankan Setup OAuth

```bash
python setup_gmail.py
```

Browser akan terbuka, login dengan akun Gmail yang akan dipakai untuk mengirim email.
Token akan disimpan otomatis sebagai `token.json`.

### 3. Konfigurasi Email

Edit `main.py` pada bagian ini:

```python
RECIPIENT_EMAIL = "email_tujuan@gmail.com"  # Email penerima rekomendasi
SENDER_EMAIL    = "email_pengirim@gmail.com" # Email Gmail yang di-authorize
```

Atau set via environment variable:

```bash
set RECIPIENT_EMAIL=email_tujuan@gmail.com
set SENDER_EMAIL=email_pengirim@gmail.com
```

### 4. Test Jalankan Manual

```bash
python main.py
```

Cek folder untuk file `preview.html` (preview email tanpa perlu kirim).

### 5. Setup Jadwal Otomatis (Hari Kerja 08:30)

Klik kanan `scheduler.bat` → **Run as administrator**

Untuk verifikasi:
```cmd
schtasks /query /tn "SahamRecommender"
```

Untuk test jalankan sekarang:
```cmd
schtasks /run /tn "SahamRecommender"
```

Untuk hapus jadwal:
```cmd
schtasks /delete /tn "SahamRecommender" /f
```

---

## Struktur File

```
saham-recommender/
├── main.py           # Script utama
├── setup_gmail.py    # Setup Gmail OAuth (jalankan sekali)
├── requirements.txt  # Python dependencies
├── scheduler.bat     # Setup Windows Task Scheduler
├── credentials.json  # (buat sendiri dari Google Cloud Console)
├── token.json        # (auto-generated setelah setup_gmail.py)
└── preview.html      # (auto-generated setiap run — preview email)
```

---

## Troubleshooting

| Problem | Solusi |
|---------|--------|
| `credentials.json not found` | Download dari Google Cloud Console (lihat step 2b) |
| `Token OAuth tidak valid` | Jalankan ulang `python setup_gmail.py` |
| Data saham kosong/sedikit | Normal jika market tutup; yfinance bisa rate-limit |
| Email tidak terkirim | Cek log error; pastikan akun Gmail di test users |
| Task Scheduler gagal | Jalankan `scheduler.bat` sebagai Administrator |

---

## Catatan Penting

- **LQ45 diperbarui** setiap Februari & Agustus oleh IDX. Update list `LQ45_SYMBOLS` di `main.py` sesuai konstituen terbaru.
- Data finansial (P/E, P/B, dll.) bersumber dari Yahoo Finance dan bisa ada keterlambatan.
- Skrip ini hanya untuk **referensi riset**, bukan saran investasi resmi.
