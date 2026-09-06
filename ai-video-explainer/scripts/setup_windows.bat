@echo off
setlocal
REM One-time Windows setup for the Local AI Video Explainer (Phase 2).
cd /d "%~dp0.."

where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python was not found on PATH. Install Python 3.10+ from python.org
    exit /b 1
)

echo [1/4] Creating virtual environment (.venv)...
python -m venv .venv
if errorlevel 1 (
    echo [ERROR] Could not create the virtual environment.
    exit /b 1
)
call .venv\Scripts\activate.bat

echo [2/4] Installing backend dependencies...
python -m pip install --upgrade pip
pip install -r backend\requirements-dev.txt
if errorlevel 1 exit /b 1

echo [3/4] Installing frontend dependencies...
pushd frontend
call npm install
if errorlevel 1 (
    popd
    exit /b 1
)
popd

echo [4/4] Creating .env from env.example (if missing)...
if not exist .env copy env.example .env >nul

echo.
echo Setup complete. Next steps:
echo   scripts\run_backend.bat    - start FastAPI on http://127.0.0.1:8000
echo   scripts\run_frontend.bat   - start the UI on http://127.0.0.1:5173
echo   scripts\run_tests.bat      - run the test suite (FFmpeg tests skip if missing)
echo.
echo FFmpeg: REQUIRED for Phase 2 uploads. Check it with:  ffmpeg -version
echo If missing, install via: winget install Gyan.FFmpeg
echo (or https://ffmpeg.org/download.html) and make sure ffmpeg/ffprobe are
endlocal
