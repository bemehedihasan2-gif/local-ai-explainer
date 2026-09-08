@echo off
setlocal
REM ============================================================
REM  setup_windows.bat - Local AI Video Explainer
REM
REM  Quick alias for the PRIMARY one-command Windows setup.
REM  The real logic lives in scripts\setup_windows_full.bat
REM  ^(venv, dependencies, .env, directories, database
REM  migrations, tests, type check, security scan and the
REM  final dependency check^). Setup is safe to repeat and
REM  never deletes your data.
REM
REM  Usage: scripts\setup_windows.bat
REM ============================================================
echo [setup] Forwarding to scripts\setup_windows_full.bat ...
echo.
call "%~dp0setup_windows_full.bat" %*
endlocal
