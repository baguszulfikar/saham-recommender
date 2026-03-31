"""
Setup Gmail OAuth untuk Saham Recommender.

Jalankan SEKALI sebelum pertama kali menggunakan main.py:
    python setup_gmail.py

Prerequisites:
  1. Buat project di Google Cloud Console
  2. Enable Gmail API
  3. Buat OAuth 2.0 credentials (Desktop App)
  4. Download credentials.json ke folder ini
"""

import json
import os
import sys

TOKEN_FILE = os.path.join(os.path.dirname(__file__), "token.json")
CREDENTIALS_FILE = os.path.join(os.path.dirname(__file__), "credentials.json")

SCOPES = ["https://www.googleapis.com/auth/gmail.send"]


def check_dependencies():
    missing = []
    try:
        import google.auth
    except ImportError:
        missing.append("google-auth")
    try:
        import google_auth_oauthlib
    except ImportError:
        missing.append("google-auth-oauthlib")
    try:
        import googleapiclient
    except ImportError:
        missing.append("google-api-python-client")

    if missing:
        print(f"[ERROR] Package belum terinstall: {', '.join(missing)}")
        print("Jalankan: pip install -r requirements.txt")
        sys.exit(1)


def setup_oauth():
    check_dependencies()

    from google_auth_oauthlib.flow import InstalledAppFlow

    if not os.path.exists(CREDENTIALS_FILE):
        print("\n[ERROR] File 'credentials.json' tidak ditemukan!")
        print("\nLangkah untuk mendapatkan credentials.json:")
        print("  1. Buka https://console.cloud.google.com/")
        print("  2. Buat project baru (atau pilih yang ada)")
        print("  3. Klik 'APIs & Services' > 'Library'")
        print("  4. Cari 'Gmail API' dan klik Enable")
        print("  5. Klik 'APIs & Services' > 'Credentials'")
        print("  6. Klik '+ Create Credentials' > 'OAuth client ID'")
        print("  7. Application type: Desktop app")
        print("  8. Download JSON dan simpan sebagai 'credentials.json'")
        print(f"     di folder: {os.path.dirname(os.path.abspath(__file__))}")
        sys.exit(1)

    print("[INFO] Memulai OAuth flow — browser akan terbuka untuk login...")
    print("       Pastikan login dengan akun Gmail yang akan dipakai untuk mengirim email.\n")

    try:
        flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
        creds = flow.run_local_server(port=0)
    except Exception as e:
        print(f"[ERROR] OAuth gagal: {e}")
        sys.exit(1)

    # Simpan token
    with open(TOKEN_FILE, "w") as f:
        f.write(creds.to_json())

    print(f"\n[OK] Token OAuth berhasil disimpan ke: {TOKEN_FILE}")
    print("[OK] Setup selesai! Sekarang Anda bisa menjalankan: python main.py")

    # Verifikasi dengan tes koneksi
    print("\n[INFO] Memverifikasi koneksi ke Gmail API...")
    try:
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build

        service = build("gmail", "v1", credentials=creds)
        profile = service.users().getProfile(userId="me").execute()
        email_addr = profile.get("emailAddress", "unknown")
        print(f"[OK] Terhubung sebagai: {email_addr}")
        print(f"\n[PERHATIAN] Update variabel di main.py:")
        print(f'  SENDER_EMAIL    = "{email_addr}"')
        print(f'  RECIPIENT_EMAIL = "tujuan@email.com"  <- ganti dengan email tujuan')
    except Exception as e:
        print(f"[WARNING] Verifikasi gagal: {e}")
        print("Token mungkin tetap valid. Coba jalankan main.py.")


if __name__ == "__main__":
    setup_oauth()
