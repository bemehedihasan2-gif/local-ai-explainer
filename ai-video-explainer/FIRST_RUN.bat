@echo off
setlocal
REM ============================================================
REM  AI VIDEO EXPLAINER - FIRST TIME SETUP
REM
REM  Double-click this file ONCE after copying the project to
REM  your Windows PC and installing the free external tools
REM  (Python, Node, FFmpeg - see docs\WINDOWS_SETUP.md).
REM
REM  It installs backend + frontend dependencies, prepares the
REM  database and folders, validates .env, runs the tests and
REM  finishes with a full system check. Missing AI models are
REM  reported with exact install instructions - the script NEVER
REM  downloads large models by itself.
REM ============================================================
cd /d "%~dp0"

echo ========================================
echo  AI VIDEO EXPLAINER
echo  FIRST TIME SETUP
echo ========================================
echo.
echo  This will:
echo   1. Check Windows / Python 3.10+ / Node.js 18+
echo   2. Create the virtual environment and install dependencies
echo   3. Prepare .env ^(kept if it already exists^), storage and the
echo      SQLite database ^(migrations^)
echo   4. Verify FFmpeg / FFprobe / Tesseract / Whisper / local LLM /
echo      Piper voices / subtitle font
echo   5. Run the backend tests and the frontend type check
echo   6. Show the final system check ^(READY TO RUN or a fix list^)
echo.
echo  Missing AI models are reported but NOT downloaded automatically.
echo  Re-running this file is always safe - it never deletes your data.
echo.

call scripts\setup_windows_full.bat /silent
set "SETUP_RC=%ERRORLEVEL%"

echo.
echo  FIRST_RUN.bat finished ^(exit %SETUP_RC%^).
echo  - If it said READY TO RUN:  double-click START_AI_VIDEO_EXPLAINER.bat
echo  - Otherwise: fix the [MISSING]/[ERROR] items it printed, then run
echo    this file again.
echo.
echo  Guides:  docs\WINDOWS_SETUP.md   docs\LOCAL_MODELS.md
echo  Troubleshooting: docs\WINDOWS_TROUBLESHOOTING.md
echo.
pause
endlocal
