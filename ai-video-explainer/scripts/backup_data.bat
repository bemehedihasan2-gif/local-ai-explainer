@echo off
setlocal
REM ============================================================
REM  backup_data.bat - Local AI Video Explainer
REM
REM  Backs up everything that matters:
REM    - data\projects\  (uploads, analysis, narration, final MP4s)
REM    - data\explainer.db  (SQLite database)
REM    - env.example + your settings summary ^(NOT your .env - it
REM      contains no secrets, but paths may be machine-specific)
REM
REM  The archive is a ZIP next to the project. It never includes
REM  .venv, node_modules, model files or logs.
REM
REM  Usage: scripts\backup_data.bat [target-folder]
REM ============================================================
cd /d "%~dp0.."

set TARGET=%~1
if "%TARGET%"=="" set TARGET=%CD%\..\ai-video-explainer-backup-%date:~-4,4%%date:~4,2%%date:~7,2%
set ZIPEXE=powershell -NoProfile -Command

echo ========================================
echo  AI VIDEO EXPLAINER - BACKUP
echo ========================================
echo.
echo  Target: %TARGET%
echo.

if not exist "data\projects" (
    echo  Nothing to back up yet ^(no data\projects folder^).
    exit /b 0
)

if exist "%TARGET%" (
    echo [ERROR] Target already exists: %TARGET%
    echo         Choose a different folder, or delete the existing one first.
    pause
    exit /b 1
)

REM ---- copy into a staging folder, then compress (no extra tools) ----
set "STAGE=%TEMP%\ave-backup-%RANDOM%"
mkdir "%STAGE%\projects" >nul 2>nul
xcopy "data\projects" "%STAGE%\projects" /E /I /Q /Y >nul
if exist "data\explainer.db" copy "data\explainer.db" "%STAGE%\explainer.db" >nul
if exist "env.example" copy "env.example" "%STAGE%\env.example.txt" >nul

REM ---- settings summary (never the .env itself) ----
> "%STAGE%\configuration-notes.txt" (
    echo AI Video Explainer backup created %date% %time%
    echo.
    echo Copy of env.example included as env.example.txt.
    echo Your live .env was NOT copied - after restoring, run
    echo scripts\configure_windows.bat to recreate defaults.
)

REM ---- compress with PowerShell Compress-Archive ----
powershell -NoProfile -Command "Compress-Archive -Path '%STAGE%\*' -DestinationPath '%TARGET%.zip' -Force"
if errorlevel 1 (
    echo [ERROR] Could not create the zip archive.
    echo         [HOW] Make sure the target folder is writable.
    rmdir /s /q "%STAGE%" >nul 2>nul
    pause
    exit /b 1
)
rmdir /s /q "%STAGE%" >nul 2>nul

echo.
echo  Backup created:
echo    %TARGET%.zip
echo.
echo  To restore: unzip it and copy the contents back over this folder
echo  ^(projects\ -^> data\projects\, explainer.db -^> data\explainer.db^).
echo.
pause
endlocal
