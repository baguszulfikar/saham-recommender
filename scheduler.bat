@echo off
REM ============================================================
REM  Saham Recommender — Windows Task Scheduler Setup
REM  Jalankan script ini SEKALI sebagai Administrator untuk
REM  mendaftarkan jadwal harian (hari kerja, jam 08:30)
REM ============================================================

SET SCRIPT_DIR=%~dp0
SET PYTHON_PATH=python
SET MAIN_SCRIPT=%SCRIPT_DIR%main.py
SET TASK_NAME=SahamRecommender

echo [INFO] Mendaftarkan scheduled task: %TASK_NAME%
echo [INFO] Script: %MAIN_SCRIPT%
echo [INFO] Jadwal: Setiap hari kerja (Senin-Jumat) pukul 08:30
echo.

REM Hapus task lama jika ada
schtasks /delete /tn "%TASK_NAME%" /f >nul 2>&1

REM Buat task baru
REM  /sc WEEKLY  : mingguan, tapi dengan /d MON,TUE,WED,THU,FRI = hari kerja
REM  /st 08:30   : jam 08:30 pagi
schtasks /create ^
  /tn "%TASK_NAME%" ^
  /tr "\"%PYTHON_PATH%\" \"%MAIN_SCRIPT%\"" ^
  /sc WEEKLY ^
  /d MON,TUE,WED,THU,FRI ^
  /st 08:30 ^
  /rl HIGHEST ^
  /f

IF %ERRORLEVEL% EQU 0 (
    echo.
    echo [OK] Scheduled task berhasil dibuat!
    echo [OK] Rekomendasi akan dikirim setiap hari kerja pukul 08:30.
    echo.
    echo Untuk melihat task: schtasks /query /tn "%TASK_NAME%"
    echo Untuk hapus task  : schtasks /delete /tn "%TASK_NAME%" /f
    echo Untuk jalankan sekarang: schtasks /run /tn "%TASK_NAME%"
) ELSE (
    echo.
    echo [ERROR] Gagal membuat scheduled task.
    echo         Pastikan script ini dijalankan sebagai Administrator.
    echo         Klik kanan scheduler.bat > "Run as administrator"
)

pause
