@echo off
setlocal
REM ============================================================
REM  health_check.bat - Local AI Video Explainer
REM
REM  Verifies the RUNNING app against the live endpoints:
REM    http://127.0.0.1:8000/api/health
REM    http://127.0.0.1:8000/api/system/status
REM    http://127.0.0.1:8000/api/system/preflight
REM
REM  Start the app first ^(START_AI_VIDEO_EXPLAINER.bat^).
REM  Usage: scripts\health_check.bat [base_url]
REM ============================================================
cd /d "%~dp0.."

set BASE_URL=%~1
if "%BASE_URL%"=="" set BASE_URL=http://127.0.0.1:8000

set PYEXE=python
if exist ".venv\Scripts\python.exe" set PYEXE=.venv\Scripts\python.exe

echo ========================================
echo  AI VIDEO EXPLAINER - HEALTH CHECK
echo  %BASE_URL%
echo ========================================
echo.

"%PYEXE%" scripts\_health_report.py %BASE_URL%
set "RC=%ERRORLEVEL%"

echo.
echo ========================================
if %RC% EQU 0 (
    echo  RESULT: PASS
) else (
    echo  RESULT: BACKEND NOT REACHABLE - start the app first
    echo  ^(START_AI_VIDEO_EXPLAINER.bat^)
)
echo ========================================
echo.
pause
endlocal
