@echo off
setlocal
REM One-time Whisper model download for Phase 4 speech-to-text (Windows).
REM Explicit download only - the app never downloads models silently.
REM Usage: scripts\download_whisper_model.bat [tiny|base]   (default: tiny)
cd /d "%~dp0.."

set MODEL=%~1
if "%MODEL%"=="" set MODEL=tiny
if not "%MODEL%"=="tiny" if not "%MODEL%"=="base" (
    echo [ERROR] Model must be "tiny" or "base" (got "%MODEL%").
    exit /b 1
)

echo Downloading faster-whisper "%MODEL%" into models\whisper\%MODEL%\ ...
echo This is a one-time download (no API key). tiny ~= 75 MB, base ~= 145 MB.

python -m pip install huggingface_hub
if errorlevel 1 exit /b 1

python -c "from huggingface_hub import snapshot_download; snapshot_download(repo_id='Systran/faster-whisper-%MODEL%', local_dir='models/whisper/%MODEL%')"
if errorlevel 1 (
    echo [ERROR] Download failed. Check your internet connection and retry.
    exit /b 1
)

echo.
echo Done. Restart the backend and re-run Analyze; speech-to-text will now run.
echo (WHISPER_MODEL=%MODEL% in .env selects which model is used.)
endlocal