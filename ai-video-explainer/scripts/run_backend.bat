@echo off
setlocal EnableDelayedExpansion
REM ============================================================
REM  run_backend.bat - Local AI Video Explainer backend
REM
REM  Runs FastAPI on http://127.0.0.1:8000 in the FOREGROUND.
REM  Press Ctrl+C to stop. ^(For the background launcher use
REM  START_AI_VIDEO_EXPLAINER.bat instead^.)
REM
REM  Usage: scripts\run_backend.bat [port]   (default from .env / 8000)
REM ============================================================
cd /d "%~dp0.."

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] .venv not found. Run FIRST_RUN.bat ^(or
    echo         scripts\setup_windows_full.bat^) first.
    exit /b 1
)

REM ---------- validate configuration loads cleanly ----------
cd backend
"..\.venv\Scripts\python.exe" -c "from app.config import get_settings; get_settings(); print('config OK')"
if errorlevel 1 (
    cd ..
    echo [ERROR] The .env configuration could not be loaded.
    echo         [HOW] Run scripts\configure_windows.bat for guidance, or fix
    echo              the invalid value ^(the Python trace above names it^).
    exit /b 1
)
cd ..

set BACKEND_PORT=8000
if not "%~1"=="" set BACKEND_PORT=%~1
if exist ".env" (
    for /f "tokens=1,* delims==" %%A in ('findstr /b "BACKEND_PORT=" .env 2^>nul') do if not "%%B"=="" if "%~1"=="" set BACKEND_PORT=%%B
)

where ffmpeg >nul 2>nul
if errorlevel 1 (
    echo [WARN] ffmpeg was not found on PATH - uploads/preprocessing/rendering
    echo        will be refused. Install FFmpeg ^(winget install Gyan.FFmpeg^)
    echo        or set FFMPEG_PATH/FFPROBE_PATH in .env.
)

echo.
echo  Starting backend:  http://127.0.0.1:%BACKEND_PORT%
echo  API docs:          http://127.0.0.1:%BACKEND_PORT%/docs
echo  Health:            http://127.0.0.1:%BACKEND_PORT%/api/health
echo  Press Ctrl+C to stop.
echo.
cd backend
"..\.venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port %BACKEND_PORT%
endlocal
