@echo off
setlocal EnableDelayedExpansion
REM ============================================================
REM  check_dependencies.bat - Local AI Video Explainer
REM
REM  Machine-gateable environment check for the full local
REM  pipeline (upload -> final MP4). Prints a PASS / WARN /
REM  ERROR report. NEVER prints secrets or full machine paths.
REM
REM  Usage:   scripts\check_dependencies.bat
REM  Exit:    0 = READY TO RUN  1 = NOT READY (fix the [ERROR] rows)
REM
REM  Also runnable through FIRST_RUN.bat / START_AI_VIDEO_EXPLAINER.bat,
REM  which stop safely when it exits 1.
REM ============================================================
cd /d "%~dp0.."
if not exist "logs" mkdir logs >nul 2>nul

set ERRORS=0
set WARNINGS=0

echo ========================================
echo  AI VIDEO EXPLAINER
echo  SYSTEM CHECK
echo  %date% %time%
echo ========================================
echo.

REM ------------------------------------------------------------
REM  Operating system (informational)
REM ------------------------------------------------------------
echo [PASS] Windows ^(64-bit:%PROCESSOR_ARCHITECTURE%^)
for /f "delims=" %%V in ('ver') do set "OSVER=%%V"
echo        %OSVER%

REM ------------------------------------------------------------
REM  Python 3.10+
REM ------------------------------------------------------------
set PYTHON_OK=0
where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python not found on PATH.
    echo         [WHY] The backend ^(FastAPI/SQLite^) needs Python 3.10+.
    echo         [WHERE] Run `python --version` in a terminal.
    echo         [HOW] Install Python 3.10+ from https://www.python.org/downloads/
    echo              and tick "Add python.exe to PATH", then open a NEW terminal.
    set /a ERRORS+=1
) else (
    python --version 2>&1 | findstr /i "Python" >nul
    python -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>nul
    if errorlevel 1 (
        echo [ERROR] Python is too old ^(need 3.10+^).
        echo         [HOW] Install a current Python from https://www.python.org/downloads/
        set /a ERRORS+=1
    ) else (
        for /f "tokens=2 delims= " %%V in ('python --version 2^>^&1') do set PYVER=%%V
        echo [PASS] Python !PYVER!
        set PYTHON_OK=1
    )
)

REM ------------------------------------------------------------
REM  pip
REM ------------------------------------------------------------
if !PYTHON_OK!==1 (
    python -m pip --version >nul 2>nul
    if errorlevel 1 (
        echo [ERROR] pip is not available.
        echo         [HOW] Reinstall Python and tick "pip" / "Add to PATH".
        set /a ERRORS+=1
    ) else (
        echo [PASS] pip
    )
)

REM ------------------------------------------------------------
REM  Virtual environment
REM ------------------------------------------------------------
if exist ".venv\Scripts\python.exe" (
    echo [PASS] Virtual environment ^(.venv^)
) else (
    echo [ERROR] Virtual environment not found ^(.venv\Scripts\python.exe^).
    echo         [WHY] Backend dependencies are installed inside .venv.
    echo         [WHERE] .venv\ in the project root.
    echo         [HOW] Run FIRST_RUN.bat ^(or scripts\setup_windows_full.bat^) once.
    set /a ERRORS+=1
)

REM ------------------------------------------------------------
REM  Node.js 18+ (frontend build)
REM ------------------------------------------------------------
set NODE_OK=0
where node >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Node.js not found on PATH.
    echo         [WHY] The web UI ^(React/Vite^) is built with Node 18+.
    echo         [HOW] Install the LTS from https://nodejs.org/ and open a NEW terminal.
    set /a ERRORS+=1
) else (
    for /f "tokens=2 delims=v." %%M in ('node --version 2^>nul') do set NODEMAJOR=%%M
    if !NODEMAJOR! LSS 18 (
        echo [ERROR] Node.js too old ^(!NODEMAJOR! - need 18+^).
        echo         [HOW] Install the LTS from https://nodejs.org/
        set /a ERRORS+=1
    ) else (
        echo [PASS] Node.js !NODEMAJOR!+ ^(node --version: `node --version`^)
        set NODE_OK=1
    )
)
if exist "frontend\node_modules" (
    echo [PASS] Frontend dependencies ^(node_modules^)
) else (
    echo [WARN] Frontend dependencies not installed ^(frontend\node_modules missing^).
    echo        [HOW] Run FIRST_RUN.bat once - it installs them with npm.
    set /a WARNINGS+=1
)

REM ------------------------------------------------------------
REM  Read .env overrides (explicit paths win over PATH)
REM ------------------------------------------------------------
set FFMPEG_PATH=
set FFPROBE_PATH=
set TESSERACT_PATH=
set TTS_EXE=
set VOICE_EN=
set VOICE_HI=
set VOICE_BN=
set LLAMA_EXE=
set LLAMA_MODEL=
set FONT_PATH=
set FONT_NAME=
set WHISPER_MODEL=tiny
set BACKEND_PORT=8000
if exist ".env" (
    for /f "tokens=1,* delims==" %%A in ('findstr /b "FFMPEG_PATH=" .env') do set "FFMPEG_PATH=%%B"
    for /f "tokens=1,* delims==" %%A in ('findstr /b "FFPROBE_PATH=" .env') do set "FFPROBE_PATH=%%B"
    for /f "tokens=1,* delims==" %%A in ('findstr /b "TESSERACT_PATH=" .env') do set "TESSERACT_PATH=%%B"
    for /f "tokens=1,* delims==" %%A in ('findstr /b "TTS_EXECUTABLE_PATH=" .env') do set "TTS_EXE=%%B"
    for /f "tokens=1,* delims==" %%A in ('findstr /b "TTS_VOICE_EN=" .env') do set "VOICE_EN=%%B"
    for /f "tokens=1,* delims==" %%A in ('findstr /b "TTS_VOICE_HI=" .env') do set "VOICE_HI=%%B"
    for /f "tokens=1,* delims==" %%A in ('findstr /b "TTS_VOICE_BN=" .env') do set "VOICE_BN=%%B"
    for /f "tokens=1,* delims==" %%A in ('findstr /b "LLAMA_CPP_PATH=" .env') do set "LLAMA_EXE=%%B"
    for /f "tokens=1,* delims==" %%A in ('findstr /b "LLAMA_MODEL_PATH=" .env') do set "LLAMA_MODEL=%%B"
    for /f "tokens=1,* delims==" %%A in ('findstr /b "SUBTITLE_FONT_PATH=" .env') do set "FONT_PATH=%%B"
    for /f "tokens=1,* delims==" %%A in ('findstr /b "SUBTITLE_FONT_NAME=" .env') do set "FONT_NAME=%%B"
    for /f "tokens=1,* delims==" %%A in ('findstr /b "WHISPER_MODEL=" .env') do if not "%%B"=="" set "WHISPER_MODEL=%%B"
    for /f "tokens=1,* delims==" %%A in ('findstr /b "BACKEND_PORT=" .env') do if not "%%B"=="" set "BACKEND_PORT=%%B"
)
REM Inline-comment guard: .env values are parsed as plain text here, so a
REM value like "tiny # comment" would leak the comment into WHISPER_MODEL.
REM Keep only the first word; metacharacters can then never break the checks.
for /f "tokens=1" %%C in ("%WHISPER_MODEL%") do set "WHISPER_MODEL=%%C"

REM ------------------------------------------------------------
REM  FFmpeg / FFprobe  (REQUIRED)
REM ------------------------------------------------------------
set FFMPEG_BIN=ffmpeg
if defined FFMPEG_PATH set "FFMPEG_BIN=%FFMPEG_PATH%"
where "%FFMPEG_BIN%" >nul 2>nul
if errorlevel 1 (
    echo [ERROR] FFmpeg not found ^(%FFMPEG_BIN%^).
    echo         [WHY] Required for upload validation, preprocessing, scene
    echo              detection, audio extraction, rendering and final MP4.
    echo         [WHERE] `ffmpeg -version` should print a version.
    echo         [HOW] winget install Gyan.FFmpeg  ^(or https://ffmpeg.org^),
    echo              then make sure ffmpeg/ffprobe are on PATH ^(new terminal^),
    echo              or set FFMPEG_PATH in .env.
    set /a ERRORS+=1
) else (
    for /f "tokens=3 delims= " %%V in ('"%FFMPEG_BIN%" -version 2^>nul ^| findstr /b "ffmpeg version"') do set FVER=%%V
    echo [PASS] FFmpeg !FVER!
)

set FFPROBE_BIN=ffprobe
if defined FFPROBE_PATH set "FFPROBE_BIN=%FFPROBE_PATH%"
where "%FFPROBE_BIN%" >nul 2>nul
if errorlevel 1 (
    echo [ERROR] FFprobe not found ^(%FFPROBE_BIN%^).
    echo         [WHY] FFprobe validates every upload (FFmpeg ships it).
    echo         [HOW] Install FFmpeg or set FFPROBE_PATH in .env.
    set /a ERRORS+=1
) else (
    echo [PASS] FFprobe
)

REM ------------------------------------------------------------
REM  Tesseract (OPTIONAL - OCR; analysis still runs without it)
REM ------------------------------------------------------------
set TESS_BIN=tesseract
if defined TESSERACT_PATH set "TESS_BIN=%TESSERACT_PATH%"
where "%TESS_BIN%" >nul 2>nul
if errorlevel 1 (
    echo [WARN] Tesseract not found - OCR will be skipped ^(optional^).
    echo        [HOW] winget install UB-Mannheim.TesseractOCR, or set TESSERACT_PATH.
    set /a WARNINGS+=1
) else (
    "%TESS_BIN%" --list-langs 2>nul | findstr /i "hin ben" >nul 2>nul
    if errorlevel 1 (
        echo [PASS] Tesseract ^(English^)
        echo [WARN] Hindi/Bengali OCR packs not detected ^(hin/ben^).
        echo        [HOW] Install them from the UB-Mannheim Tesseract installer.
        set /a WARNINGS+=1
    ) else (
        echo [PASS] Tesseract ^(English + Hindi/Bengali^)
    )
)

REM ------------------------------------------------------------
REM  Whisper (OPTIONAL - speech-to-text; skips gracefully)
REM ------------------------------------------------------------
set WHISPER_DIR=models\whisper\%WHISPER_MODEL%
if exist "%WHISPER_DIR%\model.bin" if exist "%WHISPER_DIR%\config.json" (
    echo [PASS] Whisper ^(model "%WHISPER_MODEL%" in models\whisper\%WHISPER_MODEL%^)
) else (
    echo [WARN] Whisper model not found ^(models\whisper\%WHISPER_MODEL%\model.bin^).
    echo        [WHY] Speech-to-text is optional - analysis completes without it.
    echo        [WHERE] models\whisper\%WHISPER_MODEL%\
    echo        [HOW] scripts\download_whisper_model.bat %WHISPER_MODEL%
    set /a WARNINGS+=1
)

REM ------------------------------------------------------------
REM  llama.cpp CLI (REQUIRED for story + script generation)
REM ------------------------------------------------------------
set LLAMA_BIN=llama-cli
if defined LLAMA_EXE set "LLAMA_BIN=%LLAMA_EXE%"
where "%LLAMA_BIN%" >nul 2>nul
if errorlevel 1 (
    echo [ERROR] llama.cpp ^(llama-cli^) not found ^(%LLAMA_BIN%^).
    echo         [WHY] Required for story understanding + explanation script.
    echo         [WHERE] `llama-cli --version` should print a version.
    echo         [HOW] winget install llama.cpp ^(or the GitHub release zip^),
    echo              or set LLAMA_CPP_PATH in .env to the binary.
    set /a ERRORS+=1
) else (
    echo [PASS] Llama.cpp ^(llama-cli^)
)

REM ------------------------------------------------------------
REM  GGUF model (REQUIRED)
REM ------------------------------------------------------------
set GGUF_OK=0
if defined LLAMA_MODEL (
    if exist "%LLAMA_MODEL%" (
        echo [PASS] GGUF model ^(LLAMA_MODEL_PATH set^)
        set GGUF_OK=1
    ) else (
        echo [ERROR] LLAMA_MODEL_PATH points to a missing file: %LLAMA_MODEL%
        echo         [WHERE] %LLAMA_MODEL%
        echo         [HOW] Run scripts\download_llm_model.bat and set the path
        echo              to the downloaded .gguf file.
        set /a ERRORS+=1
    )
) else (
    dir /b models\*.gguf >nul 2>nul
    if not errorlevel 1 (
        for /f %%F in ('dir /b models\*.gguf 2^>nul') do set /a GGUFCNT+=1
        if !GGUFCNT! EQU 1 (
            echo [PASS] GGUF model ^(auto-discovered in models\^)
            set GGUF_OK=1
        ) else (
            echo [ERROR] Multiple .gguf files in models\ - set LLAMA_MODEL_PATH
            echo         to choose one ^(the app requires exactly one^).
            set /a ERRORS+=1
        )
    ) else (
        dir /b models\llm\*.gguf >nul 2>nul
        if not errorlevel 1 (
            echo [ERROR] GGUF found in models\llm\ but LLAMA_MODEL_PATH is empty.
            echo         [WHY] The app auto-discovers .gguf in models\ only.
            echo         [HOW] Set in .env:
            echo              LLAMA_MODEL_PATH=models\llm\^<downloaded-file^>.gguf
        ) else (
            echo [ERROR] No GGUF model found.
            echo         [WHY] Required for story + script generation.
            echo         [WHERE] models\*.gguf ^(or set LLAMA_MODEL_PATH^).
            echo         [HOW] scripts\download_llm_model.bat  ^(~1 GB, one time^)
        )
        set /a ERRORS+=1
    )
)
set GGUFCNT=0

REM ------------------------------------------------------------
REM  Piper engine (REQUIRED for narration)
REM ------------------------------------------------------------
set PIPER_BIN=piper
if defined TTS_EXE set "PIPER_BIN=%TTS_EXE%"
where "%PIPER_BIN%" >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Piper not found ^(%PIPER_BIN%^).
    echo         [WHY] Required for local narration ^(TTS^).
    echo         [WHERE] `piper --help` should print usage.
    echo         [HOW] Install Piper ^(https://github.com/rhasspy/piper - release
    echo              zip, or `pip install piper-tts`^), put piper.exe on PATH or
    echo              set TTS_EXECUTABLE_PATH in .env.
    set /a ERRORS+=1
) else (
    echo [PASS] Piper
)

REM ------------------------------------------------------------
REM  Per-language voices (REQUIRED for that language only)
REM ------------------------------------------------------------
set VOICES_CONFIGURED=0
if defined VOICE_EN (
    if exist "%VOICE_EN%" (
        echo [PASS] English voice - READY
        set /a VOICES_CONFIGURED+=1
    ) else (
        echo [ERROR] English voice file missing: %VOICE_EN%
        echo         [HOW] Point TTS_VOICE_EN at an existing .onnx file.
        set /a ERRORS+=1
    )
) else (
    echo [WARN] English voice - NOT CONFIGURED
    echo        [HOW] scripts\setup_piper_voices.bat  ^(downloads en_US-lessac^),
    echo              then set TTS_VOICE_EN in .env to the .onnx file.
    set /a WARNINGS+=1
)
if defined VOICE_HI (
    if exist "%VOICE_HI%" (
        echo [PASS] Hindi voice - READY
        set /a VOICES_CONFIGURED+=1
    ) else (
        echo [WARN] Hindi voice file missing: %VOICE_HI%
        echo        [HOW] Fix TTS_VOICE_HI in .env ^(community Piper voice^).
        set /a WARNINGS+=1
    )
) else (
    echo [WARN] Hindi voice - NOT CONFIGURED ^(needed only for Hindi narration^)
    echo        [HOW] scripts\setup_piper_voices.bat hi\hi_IN\...\medium\....onnx
    set /a WARNINGS+=1
)
if defined VOICE_BN (
    if exist "%VOICE_BN%" (
        echo [PASS] Bengali voice - READY
        set /a VOICES_CONFIGURED+=1
    ) else (
        echo [WARN] Bengali voice file missing: %VOICE_BN%
        echo        [HOW] Fix TTS_VOICE_BN in .env ^(community Piper voice^).
        set /a WARNINGS+=1
    )
) else (
    echo [WARN] Bengali voice - NOT CONFIGURED ^(needed only for Bengali narration^)
    echo        [HOW] scripts\setup_piper_voices.bat bn\bn_IN\...\medium\....onnx
    set /a WARNINGS+=1
)
if !ERRORS! EQU 0 if !VOICES_CONFIGURED! EQU 0 (
    echo [ERROR] No Piper voice is configured for any language.
    echo         [HOW] Configure at least one TTS_VOICE_^<LANG^> in .env ^(English
    echo              recommended first^).
    set /a ERRORS+=1
)

REM ------------------------------------------------------------
REM  Subtitle font (needed for Hindi/Bengali burn-in; English
REM  renders with the default sans font)
REM ------------------------------------------------------------
set FONT_OK=0
if defined FONT_PATH if exist "%FONT_PATH%" set FONT_OK=1
if defined FONT_NAME if not "!FONT_NAME!"=="" set FONT_OK=1
if !FONT_OK!==1 (
    echo [PASS] Subtitle font
) else (
    if exist "%SystemRoot%\Fonts\Nirmala.ttc" (
        echo [PASS] Subtitle font ^(Nirmala.ttc available - set
        echo        SUBTITLE_FONT_PATH=%%SystemRoot%%\Fonts\Nirmala.ttc in .env
        echo        for Hindi/Bengali burn-in^)
    ) else (
        echo [WARN] Subtitle font not configured.
        echo        [WHY] English burn-in works without it; Hindi/Bengali burn-in
        echo             requires SUBTITLE_FONT_PATH or SUBTITLE_FONT_NAME.
        echo        [HOW] Set SUBTITLE_FONT_PATH=C:\Windows\Fonts\Nirmala.ttc ^(or
        echo             a Noto font^) in .env.
        set /a WARNINGS+=1
    )
)

REM ------------------------------------------------------------
REM  .env configuration
REM ------------------------------------------------------------
if exist ".env" (
    echo [PASS] Environment configuration ^(.env^)
) else (
    echo [WARN] .env not found - defaults will be used ^(copy env.example to
    echo        .env if you want to customize paths/voices^).
    set /a WARNINGS+=1
)

REM ------------------------------------------------------------
REM  Database + storage directories
REM ------------------------------------------------------------
set DB_OK=1
for %%D in (data data\uploads data\projects data\temp data\outputs data\cache models models\whisper models\llm models\voices logs) do (
    if exist "%%D" (
        echo [PASS] Storage ^(%%D exists^)
    ) else (
        echo [WARN] Directory missing: %%D - created by FIRST_RUN.bat.
        set /a WARNINGS+=1
    )
)
if exist ".venv\Scripts\python.exe" (
    cd backend
    "..\.venv\Scripts\python.exe" -c "from app.config import get_settings; from app.database.connection import Database; s=get_settings(); Database(s.database_path).initialize(); print('DBOK')" >nul 2>nul
    if errorlevel 1 (
        cd ..
        echo [ERROR] Database could not be initialized ^(see logs\errors.log after
        echo         a backend start^).
        set /a ERRORS+=1
    ) else (
        cd ..
        echo [PASS] Database ^(SQLite, migrations applied^)
    )
) else (
    echo [WARN] Database check skipped ^(no .venv yet^).
    set /a WARNINGS+=1
)

REM ------------------------------------------------------------
REM  Hardware: RAM, CPU, disk (informational + disk floor)
REM ------------------------------------------------------------
for /f "delims=" %%A in ('powershell -NoProfile -Command "(Get-CimInstance Win32_Processor).Name" 2^>nul') do set CPUNAME=%%A
if defined CPUNAME (echo [PASS] CPU: !CPUNAME!) else (echo [WARN] CPU info unavailable)
for /f "delims=" %%A in ('powershell -NoProfile -Command "[math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory/1GB,1)" 2^>nul') do set RAMGB=%%A
if defined RAMGB (
    if !RAMGB! LSS 4 (
        echo [WARN] RAM: !RAMGB! GB ^- below the recommended 8 GB; the app may be slow.
        set /a WARNINGS+=1
    ) else (
        echo [PASS] RAM: !RAMGB! GB
    )
) else (
    echo [WARN] RAM info unavailable.
    set /a WARNINGS+=1
)
for /f "delims=" %%A in ('powershell -NoProfile -Command "[math]::Round((Get-PSDrive -Name (Get-Location).Drive.Root.TrimEnd([char]58)).Free/1GB,1)" 2^>nul') do set FREE_GB=%%A
if defined FREE_GB (
    if !FREE_GB! LSS 1 (
        echo [ERROR] Free disk space: !FREE_GB! GB - below the 1 GB floor.
        echo         [HOW] Free space on the project drive, then re-run.
        set /a ERRORS+=1
    ) else (
        echo [PASS] Free disk space: !FREE_GB! GB
    )
) else (
    echo [WARN] Free disk space could not be measured.
    set /a WARNINGS+=1
)

REM ------------------------------------------------------------
REM  Summary
REM ------------------------------------------------------------
echo.
echo ========================================
echo  RESULT
echo ========================================
if %ERRORS% GTR 0 (
    echo  NOT READY
    echo.
    echo  Missing required components ^(%ERRORS% error^(s^), %WARNINGS% warning^(s^)^):
    echo  Fix the [ERROR] rows above - each prints WHAT/WHY/WHERE/HOW.
    echo  Warnings are optional components; the pipeline skips them cleanly.
    echo ========================================
    >> "logs\diagnostic.log" 2>nul echo [%date% %time%] check_dependencies: NOT READY ^(%ERRORS% errors, %WARNINGS% warnings^)
    exit /b 1
) else (
    echo  READY TO RUN
    if %WARNINGS% GTR 0 (
        echo  ^(%WARNINGS% optional warning^(s^) - the pipeline will skip them^)
    )
    echo.
    echo  Next: START_AI_VIDEO_EXPLAINER.bat
    echo ========================================
    >> "logs\diagnostic.log" 2>nul echo [%date% %time%] check_dependencies: READY TO RUN ^(%WARNINGS% warnings^)
    exit /b 0
)
endlocal
