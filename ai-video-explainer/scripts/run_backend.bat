@echo off
setlocal
cd /d "%~dp0..\backend"
if not exist "..\.venv\Scripts\python.exe" (
    echo [ERROR] .venv not found. Run scripts\setup_windows.bat first.
    exit /b 1
)
echo Starting backend at http://127.0.0.1:8000  (docs at /docs)
"..\.venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
endlocal
