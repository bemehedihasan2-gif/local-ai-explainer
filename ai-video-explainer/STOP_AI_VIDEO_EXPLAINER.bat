@echo off
setlocal
REM ============================================================
REM  AI VIDEO EXPLAINER - STOP
REM
REM  Stops ONLY the backend/frontend console windows started by
REM  START_AI_VIDEO_EXPLAINER.bat ^(window titles "AVE-Backend"
REM  and "AVE-Frontend"^). It never touches unrelated Python,
REM  Node or other programs.
REM
REM  Usage: STOP_AI_VIDEO_EXPLAINER.bat [/silent]
REM  (/silent skips the final pause - used by the RESTART script)
REM ============================================================
cd /d "%~dp0"
if not exist "logs" mkdir logs >nul 2>nul

echo ========================================
echo  AI VIDEO EXPLAINER - STOP
echo ========================================
echo.

taskkill /FI "WINDOWTITLE eq AVE-Backend*" /T /F >nul 2>nul
if errorlevel 1 (
    echo  Backend:  not running ^(no AVE-Backend window found^).
) else (
    echo  Backend:  stopped.
    >> "logs\launch.log" echo [%date% %time%] Backend stopped by STOP_AI_VIDEO_EXPLAINER.bat
)

taskkill /FI "WINDOWTITLE eq AVE-Frontend*" /T /F >nul 2>nul
if errorlevel 1 (
    echo  Frontend: not running ^(no AVE-Frontend window found^).
) else (
    echo  Frontend: stopped.
    >> "logs\launch.log" echo [%date% %time%] Frontend stopped by STOP_AI_VIDEO_EXPLAINER.bat
)

echo.
echo  Your videos and generated files under data\ are untouched.
echo  If you started the backend/frontend manually ^(their own console
echo  windows^), close those windows with Ctrl+C instead.
echo.
if /i not "%~1"=="/silent" pause
endlocal
