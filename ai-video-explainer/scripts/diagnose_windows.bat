@echo off
setlocal EnableDelayedExpansion
REM ============================================================
REM  Phase 8 diagnostic - Local AI Video Explainer (Windows)
REM
REM  Checks Python, pip, FFmpeg, FFprobe, Tesseract, Piper,
REM  llama.cpp, model files, RAM, CPU, disk space, .env config
REM  and required directories. Never prints secrets or API keys.
REM
REM  Usage:   scripts\diagnose_windows.bat
REM  Exit:    0 = no errors, 1 = at least one required item missing
REM ============================================================
cd /d "%~dp0.."

set ERRORS=0
set WARNINGS=0

echo ============================================================
echo  Local AI Video Explainer - environment diagnostic
echo  %date% %time%
echo ============================================================
echo.

REM ------------------------------------------------------------
REM  Python
REM ------------------------------------------------------------
where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python not found on PATH. Install Python 3.10+ from python.org
    set /a ERRORS+=1
) else (
    for /f "tokens=1 delims= " %%V in ('python --version 2^>^&1') do set PY_MAJOR=%%V
    python --version 2>&1
    python -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>nul
    if errorlevel 1 (
        echo [ERROR] Python 3.10+ is required.
        set /a ERRORS+=1
    ) else (
        echo [OK] Python version is supported.
    )
)

REM ------------------------------------------------------------
REM  pip
REM ------------------------------------------------------------
python -m pip --version >nul 2>nul
if errorlevel 1 (
    echo [ERROR] pip is not available. Reinstall Python with pip enabled.
    set /a ERRORS+=1
) else (
    echo [OK] pip is available.
)

REM ------------------------------------------------------------
REM  Virtual environment
REM ------------------------------------------------------------
if exist ".venv\Scripts\python.exe" (
    echo [OK] Virtual environment found ^(.venv^).
) else (
    echo [WARN] .venv not found - run scripts\setup_windows.bat first.
    set /a WARNINGS+=1
)

REM ------------------------------------------------------------
REM  FFmpeg / FFprobe  (explicit paths from .env win over PATH)
REM ------------------------------------------------------------
set FFMPEG_PATH=
set FFPROBE_PATH=
if exist ".env" (
    for /f "tokens=1,* delims==" %%A in ('findstr /b "FFMPEG_PATH=" .env') do set FFMPEG_PATH=%%B
    for /f "tokens=1,* delims==" %%A in ('findstr /b "FFPROBE_PATH=" .env') do set FFPROBE_PATH=%%B
)

set FFMPEG_BIN=ffmpeg
if defined FFMPEG_PATH set FFMPEG_BIN=%FFMPEG_PATH%
where "%FFMPEG_BIN%" >nul 2>nul
if errorlevel 1 (
    echo [ERROR] FFmpeg not found ^(%FFMPEG_BIN%^).
    echo         Install via "winget install Gyan.FFmpeg" or https://ffmpeg.org,
    echo         or set FFMPEG_PATH in .env
    set /a ERRORS+=1
) else (
    for /f "tokens=1,2,3" %%A in ('"%FFMPEG_BIN%" -version 2^>nul ^| findstr /b "ffmpeg version"') do (
        echo [OK] FFmpeg %%C
    )
)

set FFPROBE_BIN=ffprobe
if defined FFPROBE_PATH set FFPROBE_BIN=%FFPROBE_PATH%
where "%FFPROBE_BIN%" >nul 2>nul
if errorlevel 1 (
    echo [ERROR] FFprobe not found ^(%FFPROBE_BIN%^).
    echo         Install FFmpeg (includes ffprobe) or set FFPROBE_PATH in .env
    set /a ERRORS+=1
) else (
    for /f "tokens=1,2,3" %%A in ('"%FFPROBE_BIN%" -version 2^>nul ^| findstr /b "ffprobe version"') do (
        echo [OK] FFprobe %%C
    )
)

REM ------------------------------------------------------------
REM  Tesseract (optional - OCR)
REM ------------------------------------------------------------
set TESSERACT_PATH=
if exist ".env" (
    for /f "tokens=1,* delims==" %%A in ('findstr /b "TESSERACT_PATH=" .env') do set TESSERACT_PATH=%%B
)
set TESS_BIN=tesseract
if defined TESSERACT_PATH set TESS_BIN=%TESSERACT_PATH%
where "%TESS_BIN%" >nul 2>nul
if errorlevel 1 (
    echo [WARN] Tesseract not found - OCR will be skipped (optional).
    set /a WARNINGS+=1
) else (
    for /f "tokens=1,2,3" %%A in ('"%TESS_BIN%" --version 2^>nul ^| findstr /b "tesseract"') do (
        echo [OK] Tesseract %%C
    )
    "%TESS_BIN%" --list-langs 2>nul | findstr /i "hin ben" >nul 2>nul
    if errorlevel 1 (
        echo [WARN] Hindi/Bengali OCR language packs not detected ^(hin/ben^).
        echo         Install them from the UB-Mannheim Tesseract installer if needed.
    ) else (
        echo [OK] Hindi/Bengali OCR language packs present.
    )
)

REM ------------------------------------------------------------
REM  Piper TTS (required for narration)
REM ------------------------------------------------------------
set TTS_EXE=
if exist ".env" (
    for /f "tokens=1,* delims==" %%A in ('findstr /b "TTS_EXECUTABLE_PATH=" .env') do set TTS_EXE=%%B
)
set PIPER_BIN=piper
if defined TTS_EXE set PIPER_BIN=%TTS_EXE%
where "%PIPER_BIN%" >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Piper not found ^(%PIPER_BIN%^) - narration is disabled.
    echo         Install piper (https://github.com/rhasspy/piper) or set
    echo         TTS_EXECUTABLE_PATH in .env
    set /a ERRORS+=1
) else (
    echo [OK] Piper executable found.
)

REM per-language voices from .env
set VOICE_EN=
set VOICE_HI=
set VOICE_BN=
if exist ".env" (
    for /f "tokens=1,* delims==" %%A in ('findstr /b "TTS_VOICE_EN=" .env') do set VOICE_EN=%%B
    for /f "tokens=1,* delims==" %%A in ('findstr /b "TTS_VOICE_HI=" .env') do set VOICE_HI=%%B
    for /f "tokens=1,* delims==" %%A in ('findstr /b "TTS_VOICE_BN=" .env') do set VOICE_BN=%%B
)
if defined VOICE_EN (
    if exist "%VOICE_EN%" (echo [OK] English voice: %VOICE_EN%) else (echo [ERROR] English voice file missing: %VOICE_EN%)
    if not exist "%VOICE_EN%" set /a ERRORS+=1
) else (
    echo [WARN] TTS_VOICE_EN not set in .env - English narration disabled.
    set /a WARNINGS+=1
)
if defined VOICE_HI (
    if exist "%VOICE_HI%" (echo [OK] Hindi voice: %VOICE_HI%) else (echo [ERROR] Hindi voice file missing: %VOICE_HI%)
    if not exist "%VOICE_HI%" set /a ERRORS+=1
) else (
    echo [WARN] TTS_VOICE_HI not set in .env - Hindi narration disabled.
    set /a WARNINGS+=1
)
if defined VOICE_BN (
    if exist "%VOICE_BN%" (echo [OK] Bengali voice: %VOICE_BN%) else (echo [ERROR] Bengali voice file missing: %VOICE_BN%)
    if not exist "%VOICE_BN%" set /a ERRORS+=1
) else (
    echo [WARN] TTS_VOICE_BN not set in .env - Bengali narration disabled.
    set /a WARNINGS+=1
)

REM ------------------------------------------------------------
REM  llama.cpp local LLM (required for story + script)
REM ------------------------------------------------------------
set LLAMA_EXE=
set LLAMA_MODEL=
if exist ".env" (
    for /f "tokens=1,* delims==" %%A in ('findstr /b "LLAMA_CPP_PATH=" .env') do set LLAMA_EXE=%%B
    for /f "tokens=1,* delims==" %%A in ('findstr /b "LLAMA_MODEL_PATH=" .env') do set LLAMA_MODEL=%%B
)
set LLAMA_BIN=llama-cli
if defined LLAMA_EXE set LLAMA_BIN=%LLAMA_EXE%
where "%LLAMA_BIN%" >nul 2>nul
if errorlevel 1 (
    where llama-cli.exe >nul 2>nul
    if errorlevel 1 (
        echo [ERROR] llama.cpp ^(llama-cli^) not found - story/script generation disabled.
        echo         Build or download llama.cpp and set LLAMA_CPP_PATH in .env
        set /a ERRORS+=1
    ) else (
        echo [OK] llama-cli.exe found.
    )
) else (
    echo [OK] llama-cli found.
)
if defined LLAMA_MODEL (
    if exist "%LLAMA_MODEL%" (echo [OK] GGUF model: %LLAMA_MODEL%) else (echo [ERROR] GGUF model file missing: %LLAMA_MODEL%)
    if not exist "%LLAMA_MODEL%" set /a ERRORS+=1
) else (
    dir /b models\*.gguf >nul 2>nul
    if errorlevel 1 (
        echo [WARN] No .gguf model found in models\ - run scripts\download_llm_model.bat
        set /a WARNINGS+=1
    ) else (
        echo [OK] GGUF model found in models\:
        dir /b models\*.gguf
    )
)

REM ------------------------------------------------------------
REM  Whisper model (optional - speech-to-text)
REM ------------------------------------------------------------
dir /b models\whisper\*.bin >nul 2>nul
if errorlevel 1 (
    echo [WARN] No Whisper model found in models\whisper\ - speech analysis skipped.
    echo         Run scripts\download_whisper_model.bat to enable it.
    set /a WARNINGS+=1
) else (
    echo [OK] Whisper model present:
    dir /b models\whisper\*.bin
)

REM ------------------------------------------------------------
REM  Subtitle font for Hindi/Bengali burn-in
REM ------------------------------------------------------------
set FONT_PATH=
if exist ".env" (
    for /f "tokens=1,* delims==" %%A in ('findstr /b "SUBTITLE_FONT_PATH=" .env') do set FONT_PATH=%%B
)
if defined FONT_PATH (
    if exist "%FONT_PATH%" (
        echo [OK] Subtitle font: %FONT_PATH%
    ) else (
        echo [ERROR] SUBTITLE_FONT_PATH points to a missing file: %FONT_PATH%
        set /a ERRORS+=1
    )
) else (
    if exist "%SystemRoot%\Fonts\Nirmala.ttc" (
        echo [OK] Nirmala.ttc found - can be used for Hindi/Bengali burn-in
        echo         ^(set SUBTITLE_FONT_PATH=%%SystemRoot%%\Fonts\Nirmala.ttc in .env^)
    ) else (
        echo [WARN] SUBTITLE_FONT_PATH not set and Nirmala.ttc not found -
        echo         Hindi/Bengali subtitle burn-in will be refused until a font is set.
        set /a WARNINGS+=1
    )
)

REM ------------------------------------------------------------
REM  Hardware: CPU + RAM
REM ------------------------------------------------------------
echo.
echo --- Hardware ---
for /f "delims=" %%A in ('powershell -NoProfile -Command "(Get-CimInstance Win32_Processor).Name" 2^>nul') do echo CPU: %%A
for /f "delims=" %%A in ('powershell -NoProfile -Command "[math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory/1GB,1)" 2^>nul') do echo RAM: %%A GB GB

REM ------------------------------------------------------------
REM  Disk space on the project drive
REM ------------------------------------------------------------
echo.
echo --- Disk space ---
for /f "delims=" %%A in ('powershell -NoProfile -Command "[math]::Round((Get-PSDrive -Name (Get-Location).Drive.Root.TrimEnd([char]58)).Free/1GB,1)" 2^>nul') do set FREE_GB=%%A
if defined FREE_GB (
    echo [OK] Free space on current drive: %FREE_GB% GB
) else (
    echo [WARN] Could not determine free disk space.
    set /a WARNINGS+=1
)

REM ------------------------------------------------------------
REM  Configuration + required directories
REM ------------------------------------------------------------
echo.
echo --- Configuration ---
if exist ".env" (
    echo [OK] .env found.
    echo     Configured: FFMPEG_PATH=^(!FFMPEG_PATH!^) TTS_EXECUTABLE_PATH=^(!TTS_EXE!^)
    echo     LLAMA_CPP_PATH=^(!LLAMA_EXE!^) SUBTITLE_FONT_PATH=^(!FONT_PATH!^)
) else (
    echo [WARN] .env not found - copy env.example to .env and review the settings.
    set /a WARNINGS+=1
)

echo.
echo --- Required directories ---
for %%D in (data data\uploads data\projects data\temp data\outputs data\cache models logs) do (
    if exist "%%D" (
        echo [OK] %%D exists
    ) else (
        echo [ERROR] %%D missing - create it or re-run scripts\setup_windows.bat
        set /a ERRORS+=1
    )
)

REM ------------------------------------------------------------
REM  Summary
REM ------------------------------------------------------------
echo.
echo ============================================================
if %ERRORS% GTR 0 (
    echo  RESULT: %ERRORS% error^(s^), %WARNINGS% warning^(s^) - fix the errors above,
    echo  then re-run this script. See README Phase 8 for setup steps.
    echo ============================================================
    exit /b 1
) else (
    echo  RESULT: no errors ^(%WARNINGS% warning^(s^)^).
    if %WARNINGS% GTR 0 echo  Warnings are optional components - the pipeline will skip them.
    echo  The environment is ready for a full local pipeline run.
    echo ============================================================
    exit /b 0
)
endlocal