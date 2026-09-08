@echo off
setlocal
REM ============================================================
REM  run_frontend.bat - Local AI Video Explainer frontend
REM
REM  Runs the Vite dev server on http://127.0.0.1:5173 in the
REM  FOREGROUND. Press Ctrl+C to stop. Start the backend first
REM  ^(scripts\run_backend.bat or START_AI_VIDEO_EXPLAINER.bat^).
REM  Dependencies are installed automatically when missing.
REM ============================================================
cd /d "%~dp0..\frontend"

if not exist "node_modules" (
    echo Installing frontend dependencies with npm ^(one time^)...
    call npm install
    if errorlevel 1 (
        echo [ERROR] npm install failed. Check your internet connection.
        exit /b 1
    )
)

echo.
echo  Starting frontend:  http://127.0.0.1:5173
echo  ^(/api is proxied to the backend on http://127.0.0.1:8000^)
echo  Press Ctrl+C to stop.
echo.
call npm run dev
endlocal
