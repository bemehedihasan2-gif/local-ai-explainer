@echo off
setlocal EnableDelayedExpansion
REM ============================================================
REM  setup_windows_full.bat - Local AI Video Explainer
REM  PRIMARY one-command Windows setup.
REM
REM  Detects tools, creates the venv, installs backend + frontend
REM  dependencies, prepares directories and the SQLite database,
REM  validates .env, runs the test suite + type check + security
REM  scan and finishes with the dependency check.
REM
REM  Large AI models are NEVER downloaded automatically: missing
REM  models are reported with [MISSING]/[WHY]/[WHERE]/[HOW] and
REM  setup continues so you can fix them later.
REM
REM  Usage:
REM    scripts\setup_windows_full.bat        (interactive - pauses at the end)
REM    scripts\setup_windows_full.bat /silent (no pause; used by FIRST_RUN.bat)
REM
REM  Exit: 0 = READY TO RUN   1 = NOT READY yet (follow the printed steps)
REM ============================================================
cd /d "%~dp0.."
set "SETUP_SILENT=%~1"
set ERRORS=0
set WARNINGS=0

if not exist "logs" mkdir logs >nul 2>nul
>> "logs\setup.log" echo [%date% %time%] setup_windows_full.bat started

echo ========================================
echo  AI VIDEO EXPLAINER
echo  FIRST-TIME SETUP ^(Windows^)
echo  CPU/RAM target: Ryzen 3 3200G - 8 GB RAM
echo ========================================
echo.
echo  1. Checking Windows architecture
echo  2. Checking Python 3.10+
echo  3. Checking Node.js 18+
echo  4. Creating the virtual environment ^(.venv^)
echo  5. Installing backend Python dependencies
echo  6. Installing frontend dependencies ^(npm^)
echo  7. Preparing .env ^(keeps an existing one^)
echo  8. Creating storage directories
echo  9. Initializing the SQLite database ^(migrations^)
echo 10. Verifying FFmpeg / FFprobe
echo 11. Checking Tesseract ^(optional OCR^)
echo 12. Checking Whisper model ^(optional STT^)
echo 13. Checking the local LLM ^(llama.cpp + GGUF^)
echo 14. Checking Piper + voices
echo 15. Checking the subtitle font
echo 16. Running backend tests ^(pytest^)
echo 17. Running the frontend type check ^(tsc^)
echo 18. Running security / config checks
echo 19. Final system check
echo.

REM ------------------------------------------------------------
REM  1. Windows architecture
REM ------------------------------------------------------------
echo [1/19] Windows architecture...
if /i "%PROCESSOR_ARCHITECTURE%"=="AMD64" (
    echo        [PASS] 64-bit x86 ^(%PROCESSOR_ARCHITECTURE%^)
) else if /i "%PROCESSOR_ARCHITECTURE%"=="ARM64" (
    echo        [PASS] 64-bit ARM ^(%PROCESSOR_ARCHITECTURE%^)
) else (
    echo        [WARN] 32-bit Windows detected - a 64-bit OS is recommended.
    set /a WARNINGS+=1
)

REM ------------------------------------------------------------
REM  2. Python
REM ------------------------------------------------------------
echo [2/19] Checking Python...
set PYTHON_OK=0
where python >nul 2>nul
if errorlevel 1 (
    echo        [ERROR] Python not found on PATH.
    echo        [MISSING] Python 3.10+
    echo        [WHY] The backend ^(FastAPI/SQLite^) runs on Python.
    echo        [WHERE] `python --version` should print 3.10+.
    echo        [HOW] Install from https://www.python.org/downloads/ and tick
    echo             "Add python.exe to PATH", then open a NEW terminal.
    set /a ERRORS+=1
) else (
    python -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>nul
    if errorlevel 1 (
        echo        [ERROR] Python is too old.
        echo        [HOW] Install Python 3.10+ from https://www.python.org/downloads/
        set /a ERRORS+=1
    ) else (
        python --version
        echo        [PASS] Python 3.10+ detected.
        set PYTHON_OK=1
    )
)

REM ------------------------------------------------------------
REM  3. Node.js
REM ------------------------------------------------------------
echo [3/19] Checking Node.js...
where node >nul 2>nul
if errorlevel 1 (
    echo        [ERROR] Node.js not found on PATH.
    echo        [MISSING] Node.js 18+
    echo        [WHY] The React/Vite UI is built with Node.
    echo        [WHERE] `node --version` should print 18+.
    echo        [HOW] Install the LTS from https://nodejs.org/ and open a NEW
    echo             terminal.
    set /a ERRORS+=1
) else (
    for /f "tokens=2 delims=v." %%M in ('node --version 2^>nul') do set NODEMAJOR=%%M
    if !NODEMAJOR! LSS 18 (
        echo        [ERROR] Node.js !NODEMAJOR! is too old - need 18+.
        set /a ERRORS+=1
    ) else (
        echo        [PASS] Node.js !NODEMAJOR!+
    )
)
if not exist "frontend\package.json" (
    echo        [ERROR] frontend\package.json is missing - the project is incomplete.
    set /a ERRORS+=1
)

REM ------------------------------------------------------------
REM  4. Virtual environment
REM ------------------------------------------------------------
echo [4/19] Virtual environment ^(.venv^)...
if !PYTHON_OK!==1 (
    if exist ".venv\Scripts\python.exe" (
        echo        [PASS] .venv already exists - reusing it.
    ) else (
        python -m venv .venv
        if errorlevel 1 (
            echo        [ERROR] Could not create .venv.
            echo        [HOW] Make sure Python's venv module is available
            echo             ^(reinstall Python with "pip"/"venv" checked^).
            set /a ERRORS+=1
        ) else (
            echo        [PASS] .venv created.
        )
    )
)

REM ------------------------------------------------------------
REM  5. Backend dependencies
REM ------------------------------------------------------------
echo [5/19] Installing backend dependencies...
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -m pip install --upgrade pip >nul 2>nul
    ".venv\Scripts\python.exe" -m pip install -r backend\requirements-dev.txt
    if errorlevel 1 (
        echo        [ERROR] Backend dependency install failed ^(see pip output above^).
        echo        [HOW] Check your internet connection and re-run this setup.
        set /a ERRORS+=1
    ) else (
        echo        [PASS] Backend dependencies installed ^(fastapi, faster-whisper,
        echo              pytesseract, Pillow, pytest ^).
    )
) else (
    echo        [SKIP] No .venv - backend install skipped.
)

REM ------------------------------------------------------------
REM  6. Frontend dependencies
REM ------------------------------------------------------------
echo [6/19] Installing frontend dependencies...
if exist "frontend\package.json" (
    if exist "frontend\node_modules" (
        echo        [PASS] frontend\node_modules already present.
    ) else (
        pushd frontend
        call npm install
        if errorlevel 1 (
            popd
            echo        [ERROR] npm install failed ^(see output above^).
            set /a ERRORS+=1
        ) else (
            popd
            echo        [PASS] Frontend dependencies installed.
        )
    )
)

REM ------------------------------------------------------------
REM  7. .env (never overwrites an existing one)
REM ------------------------------------------------------------
echo [7/19] Preparing .env...
call scripts\configure_windows.bat
if errorlevel 1 set /a ERRORS+=1

REM ------------------------------------------------------------
REM  8. Storage directories
REM ------------------------------------------------------------
echo [8/19] Creating storage directories...
for %%D in (data data\uploads data\projects data\temp data\outputs data\cache models models\whisper models\llm models\voices logs) do (
    if not exist "%%D" mkdir "%%D" >nul 2>nul
    if exist "%%D" (
        echo        [PASS] %%D
    ) else (
        echo        [ERROR] Could not create %%D - check permissions.
        set /a ERRORS+=1
    )
)

REM ------------------------------------------------------------
REM  9. Database initialization (idempotent, additive migrations)
REM ------------------------------------------------------------
echo [9/19] Initializing the SQLite database...
if exist ".venv\Scripts\python.exe" (
    cd backend
    "..\.venv\Scripts\python.exe" -c "from app.config import get_settings; from app.database.connection import Database; s=get_settings(); Database(s.database_path).initialize(); print('DB_READY')"
    if errorlevel 1 (
        cd ..
        echo        [ERROR] Database initialization failed.
        echo        [HOW] Check logs\errors.log after the first backend start, or
        echo             delete data\explainer.db only if you want a fresh database.
        set /a ERRORS+=1
    ) else (
        cd ..
        echo        [PASS] SQLite database ready ^(migrations applied, existing
        echo              data preserved^).
    )
)

REM ------------------------------------------------------------
REM  10. FFmpeg / FFprobe
REM ------------------------------------------------------------
echo [10/19] Verifying FFmpeg / FFprobe...
set FFMPEG_PATH=
set FFPROBE_PATH=
if exist ".env" (
    for /f "tokens=1,* delims==" %%A in ('findstr /b "FFMPEG_PATH=" .env') do set "FFMPEG_PATH=%%B"
    for /f "tokens=1,* delims==" %%A in ('findstr /b "FFPROBE_PATH=" .env') do set "FFPROBE_PATH=%%B"
)
set FFMPEG_BIN=ffmpeg
if defined FFMPEG_PATH set FFMPEG_BIN=%FFMPEG_PATH%
where "%FFMPEG_BIN%" >nul 2>nul
if errorlevel 1 (
    echo        [MISSING] FFmpeg
    echo        [WHY] Required for upload validation, preprocessing, scene
    echo             detection, audio extraction, rendering and final MP4.
    echo        [WHERE] `ffmpeg -version` should print a version.
    echo        [HOW] winget install Gyan.FFmpeg ^(or https://ffmpeg.org^) and
    echo             open a NEW terminal, or set FFMPEG_PATH in .env.
    set /a ERRORS+=1
) else (
    "%FFMPEG_BIN%" -version 2>nul | findstr /b "ffmpeg version"
    echo        [PASS] FFmpeg found.
)
set FFPROBE_BIN=ffprobe
if defined FFPROBE_PATH set FFPROBE_BIN=%FFPROBE_PATH%
where "%FFPROBE_BIN%" >nul 2>nul
if errorlevel 1 (
    echo        [MISSING] FFprobe
    echo        [WHY] FFprobe validates every upload - it ships with FFmpeg.
    echo        [WHERE] `ffprobe -version`
    echo        [HOW] Install FFmpeg ^(includes ffprobe^) or set FFPROBE_PATH.
    set /a ERRORS+=1
) else (
    echo        [PASS] FFprobe found.
)

REM ------------------------------------------------------------
REM  11. Tesseract (optional)
REM ------------------------------------------------------------
echo [11/19] Checking Tesseract ^(optional^)...
set TESSERACT_PATH=
if exist ".env" (
    for /f "tokens=1,* delims==" %%A in ('findstr /b "TESSERACT_PATH=" .env') do set "TESSERACT_PATH=%%B"
)
set TESS_BIN=tesseract
if defined TESSERACT_PATH set TESS_BIN=%TESSERACT_PATH%
where "%TESS_BIN%" >nul 2>nul
if errorlevel 1 (
    echo        [MISSING] Tesseract ^(optional^)
    echo        [WHY] OCR on scene frames - the analysis still runs without it.
    echo        [WHERE] `tesseract --version`
    echo        [HOW] winget install UB-Mannheim.TesseractOCR, or set
    echo             TESSERACT_PATH in .env. Hindi/Bengali need the hin/ben
    echo             language packs from the same installer.
    set /a WARNINGS+=1
) else (
    "%TESS_BIN%" --list-langs 2>nul | findstr /i "hin ben" >nul 2>nul
    if errorlevel 1 (
        echo        [PASS] Tesseract ^(English^) - hin/ben packs not detected.
    ) else (
        echo        [PASS] Tesseract ^(English + Hindi/Bengali^).
    )
)

REM ------------------------------------------------------------
REM  12. Whisper (optional)
REM ------------------------------------------------------------
echo [12/19] Checking Whisper model ^(optional^)...
set WHISPER_MODEL=tiny
if exist ".env" (
    for /f "tokens=1,* delims==" %%A in ('findstr /b "WHISPER_MODEL=" .env') do if not "%%B"=="" set "WHISPER_MODEL=%%B"
)
REM Inline-comment guard: .env values are parsed as plain text here, so a
REM value like "tiny # comment" would leak the comment into WHISPER_MODEL.
REM Keep only the first word; metacharacters can then never break the checks.
for /f "tokens=1" %%C in ("%WHISPER_MODEL%") do set "WHISPER_MODEL=%%C"
if exist "models\whisper\%WHISPER_MODEL%\model.bin" (
    echo        [PASS] Whisper model "%WHISPER_MODEL%" present.
) else (
    echo        [MISSING] Whisper model "%WHISPER_MODEL%" ^(optional^)
    echo        [WHY] Speech-to-text for analyzed videos - analysis completes
    echo             without it ^(transcript simply unavailable^).
    echo        [WHERE] models\whisper\%WHISPER_MODEL%\model.bin
    echo        [HOW] scripts\download_whisper_model.bat %WHISPER_MODEL%
    echo             ^(~75 MB for tiny, one-time, no API key^).
    set /a WARNINGS+=1
)

REM ------------------------------------------------------------
REM  13. Local LLM (llama.cpp CLI + GGUF)
REM ------------------------------------------------------------
echo [13/19] Checking the local LLM...
set LLAMA_EXE=
set LLAMA_MODEL=
if exist ".env" (
    for /f "tokens=1,* delims==" %%A in ('findstr /b "LLAMA_CPP_PATH=" .env') do set "LLAMA_EXE=%%B"
    for /f "tokens=1,* delims==" %%A in ('findstr /b "LLAMA_MODEL_PATH=" .env') do set "LLAMA_MODEL=%%B"
)
set LLAMA_BIN=llama-cli
if defined LLAMA_EXE set "LLAMA_BIN=%LLAMA_EXE%"
where "%LLAMA_BIN%" >nul 2>nul
if errorlevel 1 (
    echo        [MISSING] llama.cpp CLI ^(llama-cli^)
    echo        [WHY] Story understanding + explanation generation need it.
    echo        [WHERE] `llama-cli --version`
    echo        [HOW] winget install llama.cpp ^(or the GitHub release zip^),
    echo             or set LLAMA_CPP_PATH in .env. See docs\LOCAL_MODELS.md.
    set /a ERRORS+=1
) else (
    echo        [PASS] llama.cpp CLI found.
)
set GGUF_OK=0
if defined LLAMA_MODEL (
    if exist "%LLAMA_MODEL%" (
        echo        [PASS] GGUF model ^(LLAMA_MODEL_PATH^).
        set GGUF_OK=1
    ) else (
        echo        [MISSING] GGUF model - LLAMA_MODEL_PATH points to a file
        echo        that does not exist: %LLAMA_MODEL%
        echo        [HOW] Run scripts\download_llm_model.bat or fix the path.
        set /a ERRORS+=1
    )
) else (
    dir /b models\*.gguf >nul 2>nul
    if not errorlevel 1 (
        echo        [PASS] GGUF model auto-discovered in models\.
        set GGUF_OK=1
    ) else (
        dir /b models\llm\*.gguf >nul 2>nul
        if not errorlevel 1 (
            echo        [MISSING] GGUF model
            echo        [WHY] The model sits in models\llm\ but the app only
            echo             auto-discovers models\*.gguf.
            echo        [WHERE] models\llm\*.gguf
            echo        [HOW] Set in .env:
            echo             LLAMA_MODEL_PATH=models\llm\^<file^>.gguf
        ) else (
            echo        [MISSING] GGUF model
            echo        [WHY] Story + script generation need a small quantized
            echo             model ^(~1 GB^).
            echo        [WHERE] models\*.gguf ^(or set LLAMA_MODEL_PATH^).
            echo        [HOW] scripts\download_llm_model.bat  ^(one time, no API
            echo             key^). Full guide: docs\LOCAL_MODELS.md
        )
        set /a ERRORS+=1
    )
)

REM ------------------------------------------------------------
REM  14. Piper + voices
REM ------------------------------------------------------------
echo [14/19] Checking Piper + voices...
set TTS_EXE=
set VOICE_EN=
set VOICE_HI=
set VOICE_BN=
if exist ".env" (
    for /f "tokens=1,* delims==" %%A in ('findstr /b "TTS_EXECUTABLE_PATH=" .env') do set "TTS_EXE=%%B"
    for /f "tokens=1,* delims==" %%A in ('findstr /b "TTS_VOICE_EN=" .env') do set "VOICE_EN=%%B"
    for /f "tokens=1,* delims==" %%A in ('findstr /b "TTS_VOICE_HI=" .env') do set "VOICE_HI=%%B"
    for /f "tokens=1,* delims==" %%A in ('findstr /b "TTS_VOICE_BN=" .env') do set "VOICE_BN=%%B"
)
set PIPER_BIN=piper
if defined TTS_EXE set "PIPER_BIN=%TTS_EXE%"
where "%PIPER_BIN%" >nul 2>nul
if errorlevel 1 (
    echo        [MISSING] Piper ^(TTS engine^)
    echo        [WHY] Local narration needs it ^(Phase 6^).
    echo        [WHERE] `piper --help`
    echo        [HOW] Download from https://github.com/rhasspy/piper ^(release
    echo             zip^), or `pip install piper-tts`. Set TTS_EXECUTABLE_PATH
    echo             in .env if it is not on PATH.
    set /a ERRORS+=1
) else (
    echo        [PASS] Piper engine found.
)
set VOICE_COUNT=0
if defined VOICE_EN if exist "%VOICE_EN%" (
    echo        [PASS] English voice READY.
    set /a VOICE_COUNT+=1
) else (
    echo        [MISSING] English Piper voice
    echo        [WHY] English narration needs a configured .onnx voice.
    echo        [WHERE] models\voices\en\...\en_US-....onnx
    echo        [HOW] scripts\setup_piper_voices.bat then set
    echo             TTS_VOICE_EN=models\voices\en\en_US\lessac\medium\en_US-lessac-medium.onnx
    echo             in .env. Full guide: docs\LOCAL_MODELS.md
)
if defined VOICE_HI if exist "%VOICE_HI%" (
    echo        [PASS] Hindi voice READY.
    set /a VOICE_COUNT+=1
) else (
    echo        [MISSING] Hindi voice ^(optional until you narrate in Hindi^)
    echo        [HOW] scripts\setup_piper_voices.bat hi\hi_IN\^<voice^>\medium\...onnx
    echo             then set TTS_VOICE_HI in .env.
    set /a WARNINGS+=1
)
if defined VOICE_BN if exist "%VOICE_BN%" (
    echo        [PASS] Bengali voice READY.
    set /a VOICE_COUNT+=1
) else (
    echo        [MISSING] Bengali voice ^(optional until you narrate in Bengali^)
    echo        [HOW] scripts\setup_piper_voices.bat bn\bn_IN\^<voice^>\medium\...onnx
    echo             then set TTS_VOICE_BN in .env.
    set /a WARNINGS+=1
)
if !VOICE_COUNT! EQU 0 if !ERRORS! EQU 0 (
    echo        [WARN] No Piper voice configured yet - configure at least one
    echo              TTS_VOICE_<LANG> in .env before generating narration.
    set /a WARNINGS+=1
)

REM ------------------------------------------------------------
REM  15. Subtitle font
REM ------------------------------------------------------------
echo [15/19] Checking the subtitle font...
set FONT_PATH=
set FONT_NAME=
if exist ".env" (
    for /f "tokens=1,* delims==" %%A in ('findstr /b "SUBTITLE_FONT_PATH=" .env') do set "FONT_PATH=%%B"
    for /f "tokens=1,* delims==" %%A in ('findstr /b "SUBTITLE_FONT_NAME=" .env') do set "FONT_NAME=%%B"
)
set FONT_OK=0
if defined FONT_PATH if exist "%FONT_PATH%" set FONT_OK=1
if defined FONT_NAME if not "!FONT_NAME!"=="" set FONT_OK=1
if !FONT_OK!==1 (
    echo        [PASS] Subtitle font configured.
) else (
    if exist "%SystemRoot%\Fonts\Nirmala.ttc" (
        echo        [PASS] Nirmala.ttc is available. For Hindi/Bengali burn-in,
        echo              add to .env:
        echo              SUBTITLE_FONT_PATH=C:\Windows\Fonts\Nirmala.ttc
    ) else (
        echo        [MISSING] Subtitle font ^(only needed for hi/bn burn-in^)
        echo        [WHY] English burn-in uses the default sans font; Hindi and
        echo             Bengali glyphs need a Unicode font or the render
        echo             refuses with instructions ^(never tofu boxes^).
        echo        [HOW] Set SUBTITLE_FONT_PATH=C:\Windows\Fonts\Nirmala.ttc
        echo             ^(or a Noto Sans Devanagari/Bengali font^) in .env.
        set /a WARNINGS+=1
    )
)

REM ------------------------------------------------------------
REM  16. Backend tests
REM ------------------------------------------------------------
echo [16/19] Running the backend test suite...
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -m pytest backend\tests -q
    if errorlevel 1 (
        echo        [ERROR] Some backend tests FAILED ^(see the report above^).
        set /a ERRORS+=1
    ) else (
        echo        [PASS] Backend test suite passed ^(real-media tests skip
        echo              cleanly when FFmpeg is absent - they run on your PC^).
    )
) else (
    echo        [SKIP] No .venv yet.
)

REM ------------------------------------------------------------
REM  17. Frontend type check
REM ------------------------------------------------------------
echo [17/19] Running the frontend type check...
if exist "frontend\node_modules" (
    pushd frontend
    call npm run typecheck
    if errorlevel 1 (
        popd
        echo        [ERROR] Frontend TypeScript check FAILED.
        set /a ERRORS+=1
    ) else (
        popd
        echo        [PASS] Frontend TypeScript check passed.
    )
) else (
    echo        [SKIP] node_modules missing - run FIRST_RUN.bat again.
)

REM ------------------------------------------------------------
REM  18. Security / configuration checks
REM ------------------------------------------------------------
echo [18/19] Security / config checks...
set SEC_VIOLATION=0
findstr /s /i /c:"shell=True" backend\app\*.py >nul 2>nul
if not errorlevel 1 (
    echo        [WARN] "shell=True" found in backend\app - review the file(s).
    set SEC_VIOLATION=1
)
findstr /s /i /c:"os.system" backend\app\*.py >nul 2>nul
if not errorlevel 1 (
    echo        [WARN] "os.system" found in backend\app - review the file(s).
    set SEC_VIOLATION=1
)
findstr /s /i /c:"eval^(" backend\app\*.py >nul 2>nul
if not errorlevel 1 (
    echo        [WARN] "eval^(" found in backend\app - review the file(s).
    set SEC_VIOLATION=1
)
if !SEC_VIOLATION! EQU 0 echo        [PASS] No shell=True / os.system / eval^( patterns in backend.
findstr /s /i /c:"api_key" frontend\src\*.ts frontend\src\*.tsx >nul 2>nul
if not errorlevel 1 (
    echo        [WARN] "api_key" appears in frontend\src - review the file(s).
    set /a WARNINGS+=1
) else (
    echo        [PASS] No api_key references in the frontend.
)
echo        [PASS] .env is gitignored ^(never committed^).

REM ------------------------------------------------------------
REM  19. Final system check
REM ------------------------------------------------------------
echo [19/19] Final system check...
call scripts\check_dependencies.bat
if errorlevel 1 set /a ERRORS+=1

echo.
echo ========================================
echo  SETUP COMPLETE
echo ========================================
if %ERRORS% GTR 0 (
    echo  NOT READY TO RUN ^(%ERRORS% error^(s^), %WARNINGS% warning^(s^)^).
    echo  Read the [MISSING]/[ERROR] blocks above: each explains WHAT is
    echo  missing, WHY it matters, WHERE to look and HOW to fix it.
    echo  Optional warnings ^(Whisper/OCR/Hindi/Bengali voices/font^) do not
    echo  block the English 2/3/4-minute pipeline.
    >> "logs\setup.log" echo [%date% %time%] setup finished: NOT READY ^(%ERRORS% errors, %WARNINGS% warnings^)
    echo.
    if /i not "%SETUP_SILENT%"=="/silent" pause
    exit /b 1
) else (
    echo  READY TO RUN
    echo.
    echo  Next step: double-click START_AI_VIDEO_EXPLAINER.bat
    echo  ^(backend on http://127.0.0.1:8000, UI on http://127.0.0.1:5173^)
    >> "logs\setup.log" echo [%date% %time%] setup finished: READY TO RUN ^(%WARNINGS% warnings^)
    echo.
    if /i not "%SETUP_SILENT%"=="/silent" pause
    exit /b 0
)
endlocal
