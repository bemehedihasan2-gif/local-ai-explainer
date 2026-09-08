@echo off
setlocal EnableDelayedExpansion
REM ============================================================
REM  AI VIDEO EXPLAINER - RESTART
REM
REM  STOP  -> wait for the app to release its ports -> START
REM  Your data under data\ is never touched.
REM ============================================================
cd /d "%~dp0"

echo ========================================
echo  AI VIDEO EXPLAINER - RESTART
echo ========================================
echo.
echo  STEP 1 of 3: stopping the running app...
call STOP_AI_VIDEO_EXPLAINER.bat /silent

echo.
echo  STEP 2 of 3: waiting for the app to stop...
set /a TRIES=0
:wait_down
set /a TRIES+=1
curl -s --max-time 2 http://127.0.0.1:8000/api/health >nul 2>&1
if errorlevel 1 goto is_down
if !TRIES! GEQ 30 (
    echo  The backend is still answering after 30 seconds.
    echo  It may have been started manually - close its window with Ctrl+C,
    echo  then run START_AI_VIDEO_EXPLAINER.bat.
    pause
    exit /b 1
)
timeout /t 1 /nobreak >nul
goto wait_down
:is_down
echo  The app has stopped.

echo.
echo  STEP 3 of 3: starting the app again...
call START_AI_VIDEO_EXPLAINER.bat
endlocal
