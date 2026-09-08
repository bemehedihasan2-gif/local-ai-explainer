@echo off
setlocal EnableDelayedExpansion
REM ============================================================
REM  configure_windows.bat - Local AI Video Explainer
REM
REM  Creates .env from env.example (only when .env is missing -
REM  an existing .env is NEVER overwritten), then validates the
REM  configuration by loading it through the backend Settings.
REM
REM  Safe CPU-first defaults for the Ryzen 3 3200G + 8 GB target
REM  are already the defaults in env.example (concurrency 1,
REM  LLAMA_THREADS=4, LLAMA_CONTEXT_SIZE=2048, ANALYSIS_WIDTH=640,
REM  ANALYSIS_FPS=5, THUMBNAIL_WIDTH=320, AUDIO_SAMPLE_RATE=16000).
REM  This script only warns when an existing .env deviates from
REM  them - it never rewrites user configuration.
REM
REM  Never prints secrets (the app has none - no API keys exist).
REM  Usage: scripts\configure_windows.bat
REM ============================================================
cd /d "%~dp0.."

echo [configure] Ensuring .env exists...

if exist ".env" (
    echo [configure] .env already present - keeping your configuration.
) else (
    if not exist "env.example" (
        echo [ERROR] env.example is missing - the project may be incomplete.
        exit /b 1
    )
    copy env.example .env >nul
    if errorlevel 1 (
        echo [ERROR] Could not create .env from env.example.
        exit /b 1
    )
    echo [configure] Created .env from env.example.
)

REM ------------------------------------------------------------
REM  Guard: an empty FFMPEG_PATH= line in .env is an override to
REM  nothing (fine - the app then discovers ffmpeg on PATH), but
REM  warn when the user set values that look like secrets.
REM ------------------------------------------------------------
findstr /i /b "API_KEY SECRET TOKEN PASSWORD" .env >nul 2>nul
if not errorlevel 1 (
    echo [WARN] .env contains lines starting with API_KEY/SECRET/TOKEN/PASSWORD.
    echo        This application is fully local and needs NO secrets - remove
    echo        any keys you pasted from the cloud-AI world.
)

REM ------------------------------------------------------------
REM  Validate the configuration through the backend Settings
REM  (this also proves the venv Python + dependencies work).
REM ------------------------------------------------------------
if not exist ".venv\Scripts\python.exe" (
    echo [WARN] .venv not found - skipping Settings validation.
    echo        Run FIRST_RUN.bat to create it first.
) else (
    cd backend
    "..\.venv\Scripts\python.exe" -c "from app.config import Settings; s = Settings(); assert s.processing_concurrency >= 1; assert s.llama_threads >= 1; assert s.llama_context_size >= 512; print('CONFIG_OK')" >nul 2>nul
    if errorlevel 1 (
        cd ..
        echo [ERROR] .env could not be loaded by the backend Settings.
        echo        A value is invalid (e.g. WHISPER_MODEL not tiny/base, an
        echo        out-of-range timeout, a bad JSON list). Check the exact
        echo        message by running:
        echo          cd backend
        echo          ..\.venv\Scripts\python.exe -c "from app.config import Settings; Settings()"
        exit /b 1
    )
    cd ..
    echo [configure] .env validated against the backend Settings ^(CONFIG_OK^).
)

REM ------------------------------------------------------------
REM  Recommended Ryzen 3 3200G settings - warn (never change)
REM  when an existing .env deviates from the safe CPU defaults.
REM ------------------------------------------------------------
for /f "tokens=1,* delims==" %%A in ('findstr /b "PROCESSING_CONCURRENCY=" .env 2^>nul') do if not "%%B"=="" set CUR_CONC=%%B
if defined CUR_CONC if not "!CUR_CONC!"=="1" (
    echo [WARN] PROCESSING_CONCURRENCY=!CUR_CONC! ^(recommended 1 on 8 GB RAM^).
    echo        Higher concurrency can run several heavy jobs at once and
    echo        exhaust memory on the target machine.
)
for /f "tokens=1,* delims==" %%A in ('findstr /b "LLAMA_THREADS=" .env 2^>nul') do if not "%%B"=="" set CUR_THREADS=%%B
if defined CUR_THREADS if !CUR_THREADS! GTR 4 (
    echo [WARN] LLAMA_THREADS=!CUR_THREADS! ^(recommended 4 on a 4-core CPU^).
)

echo.
echo [configure] Done. Your .env is ready.
exit /b 0
endlocal
