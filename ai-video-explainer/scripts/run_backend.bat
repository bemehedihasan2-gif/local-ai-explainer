@echo off
setlocal
cd /d "%~dp0..\backend"
if not exist "..\.venv\Scripts\python.exe" (
    echo [ERROR] .venv not found. Run scripts\setup_windows.bat first.
    exit /b 1
)
where ffmpeg >nul 2>nul
if errorlevel 1 (
    echo [WARN] ffmpeg was not found on PATH - video uploads will be rejected.
    echo        Install FFmpeg (winget install Gyan.FFmpeg) or set FFMPEG_PATH/FFPROBE_PATH in .env
)
echo Starting backend at http://127.0.0.1:8000  (docs at /docs)
"..\.venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
endlocal
