@echo off
setlocal EnableDelayedExpansion
REM ============================================================
REM  uninstall_app.bat - Local AI Video Explainer
REM
REM  Removes the generated environment:
REM    - .venv\          (Python virtual environment - reinstalled by setup)
REM    - frontend\node_modules\  (reinstalled by npm install)
REM    - .pytest_cache\  __pycache__\  data\temp\ data\cache\
REM    - logs\           (log files)
REM
REM  It NEVER deletes your videos or generated outputs unless you
REM  explicitly choose the FULL data wipe at the end:
REM    data\projects\    (your uploaded videos + analysis + final MP4s)
REM    data\explainer.db (project database)
REM
REM  Usage: scripts\uninstall_app.bat
REM ============================================================
cd /d "%~dp0.."

echo ========================================
echo  AI VIDEO EXPLAINER - UNINSTALL
echo ========================================
echo.
echo  This removes the installed environment ^(.venv, node_modules,
echo  caches, logs^). Your videos under data\projects\ are KEPT
echo  unless you choose the full wipe below.
echo.
set /p CONFIRM=Type UNINSTALL to continue: 
if not "%CONFIRM%"=="UNINSTALL" (
    echo  Cancelled - nothing was removed.
    exit /b 0
)

echo.
echo  Removing virtual environment ^(.venv^)...
if exist ".venv" (rmdir /s /q ".venv" && echo    removed .venv) else echo    not present.

echo  Removing frontend dependencies ^(node_modules^)...
if exist "frontend\node_modules" (rmdir /s /q "frontend\node_modules" && echo    removed frontend\node_modules) else echo    not present.

echo  Removing caches and bytecode...
for %%D in (.pytest_cache data\temp data\cache frontend\dist) do (
    if exist "%%D" (rmdir /s /q "%%D" 2>nul && echo    removed %%D)
)
for /f "delims=" %%F in ('dir /s /b /ad __pycache__ 2^>nul') do rmdir /s /q "%%F" 2>nul

echo  Removing logs...
if exist "logs" (
    del /q "logs\*.log" "logs\*.tmp" "logs\.health.tmp" "logs\.page.tmp" 2>nul
    echo    log files removed ^(logs\ folder kept^)
)

echo.
echo ========================================
echo  FULL DATA WIPE ^(optional^)
echo ========================================
echo.
echo  Choose this ONLY if you also want to delete every uploaded
echo  video, analysis, narration and final MP4 plus the database.
echo.
set /p WIPE=Type DELETE EVERYTHING for a full wipe, or press Enter to keep your data: 
if "%WIPE%"=="DELETE EVERYTHING" (
    echo.
    echo  Wiping data\projects\ and data\explainer.db...
    if exist "data\projects" rmdir /s /q "data\projects"
    if exist "data\explainer.db" del /q "data\explainer.db"
    echo    Full wipe done.
) else (
    echo.
    echo  Your data was KEPT:
    echo    data\projects\    - uploads, analysis, narration, final MP4s
    echo    data\explainer.db - project database
)

echo.
echo  Uninstall complete.
echo  To reinstall later: run FIRST_RUN.bat again ^(it recreates
echo  everything and keeps any data that is still present^).
echo.
pause
endlocal
