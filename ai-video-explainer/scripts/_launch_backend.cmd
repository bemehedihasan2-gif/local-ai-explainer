@echo off
REM Internal helper used by START_AI_VIDEO_EXPLAINER.bat to run the
REM backend inside its own console window (title "AVE-Backend" is set
REM by the caller). Output is appended to logs\launch.log.
REM Usage: _launch_backend.cmd [port]   (default 8000)
cd /d "%~dp0..\backend"
set "BPORT=%~1"
if "%BPORT%"=="" set "BPORT=8000"
"%~dp0..\.venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port %BPORT% >> "%~dp0..\logs\launch.log" 2>&1
