@echo off
REM Internal helper used by START_AI_VIDEO_EXPLAINER.bat to run the Vite
REM dev server inside its own console window (title "AVE-Frontend" is set
REM by the caller). Installs frontend dependencies first if missing.
REM Output is appended to logs\launch.log.
cd /d "%~dp0..\frontend"
if not exist "node_modules" (
    echo First frontend start - installing dependencies with npm...
    call npm install
)
call npm run dev >> "%~dp0..\logs\launch.log" 2>&1
