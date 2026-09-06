@echo off
setlocal
cd /d "%~dp0..\frontend"
if not exist "node_modules" (
    echo [ERROR] node_modules missing. Run scripts\setup_windows.bat first.
    exit /b 1
)
echo Starting frontend at http://127.0.0.1:5173  (start the backend first)
call npm run dev
endlocal
