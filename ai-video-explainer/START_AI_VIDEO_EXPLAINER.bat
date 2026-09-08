@echo off
setlocal EnableDelayedExpansion
REM ============================================================
REM  AI VIDEO EXPLAINER - START
REM
REM  Double-click this file after FIRST_RUN.bat has completed.
REM
REM  STEP 1  Dependency check (scripts\check_dependencies.bat).
REM  STEP 2  Start the backend on http://127.0.0.1:8000 (wait healthy).
REM  STEP 3  Start the frontend on http://127.0.0.1:5173 (wait ready).
REM  STEP 4  Open the browser.
REM
REM  If the backend/frontend are ALREADY running (started by this
REM  launcher earlier) they are reused - no duplicate processes.
REM  Only processes started by this launcher (console titles
REM  "AVE-Backend" / "AVE-Frontend") are tracked; STOP_AI_VIDEO_
REM  EXPLAINER.bat stops exactly those.
REM ============================================================
cd /d "%~dp0"

echo ========================================
echo  AI VIDEO EXPLAINER - LAUNCHER
echo ========================================
echo.

REM ------------------ read port from .env ------------------
set BACKEND_PORT=8000
if exist ".env" (
    for /f "tokens=1,* delims==" %%A in ('findstr /b "BACKEND_PORT=" .env 2^>nul') do if not "%%B"=="" set BACKEND_PORT=%%B
)
set FRONTEND_PORT=5173

REM ------------------ curl available? ------------------
where curl >nul 2>nul
if errorlevel 1 (
    echo [ERROR] curl.exe was not found ^(needed to wait for the servers^).
    echo         [WHAT] The launcher polls the app until it is ready.
    echo         [WHY] curl ships with Windows 10 1803+ and Windows 11.
    echo         [HOW] Update Windows, or install curl ^(winget install curl^).
    pause
    exit /b 1
)

REM ------------------ STEP 1: dependency gate ------------------
echo STEP 1 of 4: checking dependencies...
call scripts\check_dependencies.bat
if errorlevel 1 (
    echo.
    echo [ERROR] The system is not ready to run.
    echo         [WHAT] Required components are missing ^(see the report above^).
    echo         [WHY] The pipeline cannot start with missing required tools.
    echo         [HOW] Fix each [ERROR] row ^(each prints WHAT/WHY/WHERE/HOW^),
    echo              then run FIRST_RUN.bat once and try again.
    echo         Docs: docs\WINDOWS_SETUP.md  docs\LOCAL_MODELS.md
    pause
    exit /b 1
)
echo         Dependency check passed.
echo.

REM ------------------ STEP 2: backend ------------------
echo STEP 2 of 4: starting the backend on http://127.0.0.1:%BACKEND_PORT% ...
set "TMP_HEALTH=logs\.health.tmp"
set "BACKEND_UP=0"

curl -s --max-time 3 -o "%TMP_HEALTH%" http://127.0.0.1:%BACKEND_PORT%/api/health >nul 2>&1
if not errorlevel 1 goto backend_probe_found
goto backend_probe_free

:backend_probe_found
findstr /c:"Local AI Video Explainer" "%TMP_HEALTH%" >nul 2>nul
if not errorlevel 1 (
    echo         Backend already running - reusing it.
    set BACKEND_UP=1
    goto backend_done
)
echo [ERROR] Port %BACKEND_PORT% is occupied by ANOTHER program.
echo         [WHAT] Something else already answers on port %BACKEND_PORT%.
echo         [WHY] Two servers cannot share one port.
echo         [HOW] Stop that program, or change BACKEND_PORT in .env.
pause
exit /b 1

:backend_probe_free
echo         Port %BACKEND_PORT% is free - launching the backend...
if not exist "logs" mkdir logs >nul 2>nul
>> "logs\launch.log" echo [%date% %time%] Backend started by START_AI_VIDEO_EXPLAINER.bat
start "AVE-Backend" /min "%~dp0scripts\_launch_backend.cmd" %BACKEND_PORT%
if errorlevel 1 (
    echo [ERROR] Could not launch the backend process.
    pause
    exit /b 1
)
set /a TRIES=0

:wait_backend
set /a TRIES+=1
curl -s --max-time 2 -o "%TMP_HEALTH%" http://127.0.0.1:%BACKEND_PORT%/api/health >nul 2>&1
findstr /c:"Local AI Video Explainer" "%TMP_HEALTH%" >nul 2>nul
if not errorlevel 1 goto backend_done
if !TRIES! GEQ 45 goto backend_failed
timeout /t 2 /nobreak >nul
goto wait_backend

:backend_failed
echo [ERROR] Backend did not become healthy within 90 seconds.
echo         [WHAT] The backend process started but /api/health is not OK.
echo         [WHY] A startup error ^(check logs\launch.log and logs\errors.log^).
echo         [HOW] Open logs\launch.log, fix the reported error, then run
echo              this launcher again.
pause
exit /b 1

:backend_done
if "!BACKEND_UP!"=="1" goto backend_step_done
echo         Backend is healthy.
:backend_step_done
echo.

REM ------------------ STEP 3: frontend ------------------
echo STEP 3 of 4: starting the frontend on http://127.0.0.1:%FRONTEND_PORT% ...
set "TMP_PAGE=logs\.page.tmp"
set "FRONTEND_UP=0"

curl -s --max-time 3 -o "%TMP_PAGE%" http://127.0.0.1:%FRONTEND_PORT%/ >nul 2>&1
if not errorlevel 1 goto frontend_probe_found
goto frontend_probe_free

:frontend_probe_found
findstr /c:"AI Video Explainer" "%TMP_PAGE%" >nul 2>nul
if not errorlevel 1 (
    echo         Frontend already running - reusing it.
    set FRONTEND_UP=1
    goto frontend_done
)
echo [ERROR] Port %FRONTEND_PORT% is occupied by ANOTHER program.
echo         [WHAT] Something else already answers on port %FRONTEND_PORT%.
echo         [HOW] Stop that program ^(or edit the port in
echo              frontend\vite.config.ts AND the Vite proxy target^).
pause
exit /b 1

:frontend_probe_free
echo         Port %FRONTEND_PORT% is free - launching the frontend...
>> "logs\launch.log" echo [%date% %time%] Frontend started by START_AI_VIDEO_EXPLAINER.bat
start "AVE-Frontend" /min "%~dp0scripts\_launch_frontend.cmd"
if errorlevel 1 (
    echo [ERROR] Could not launch the frontend process.
    pause
    exit /b 1
)
set /a TRIES=0

:wait_frontend
set /a TRIES+=1
curl -s --max-time 2 -o "%TMP_PAGE%" http://127.0.0.1:%FRONTEND_PORT%/ >nul 2>&1
findstr /c:"AI Video Explainer" "%TMP_PAGE%" >nul 2>nul
if not errorlevel 1 goto frontend_done
if !TRIES! GEQ 45 goto frontend_failed
timeout /t 2 /nobreak >nul
goto wait_frontend

:frontend_failed
echo [ERROR] Frontend did not become ready within 90 seconds.
echo         [WHAT] The Vite process started but the page is not served.
echo         [WHY] A startup error ^(check logs\launch.log^).
echo         [HOW] Open logs\launch.log, fix the reported error, then run
echo              this launcher again.
pause
exit /b 1

:frontend_done
if "!FRONTEND_UP!"=="0" echo         Frontend is ready.
echo.

REM ------------------ STEP 4: open the browser ------------------
echo STEP 4 of 4: opening your browser...
start "" "http://127.0.0.1:%FRONTEND_PORT%"

echo.
echo ========================================
echo  AI VIDEO EXPLAINER IS RUNNING
echo ========================================
echo.
echo   UI:       http://127.0.0.1:%FRONTEND_PORT%
echo   Backend:  http://127.0.0.1:%BACKEND_PORT%   ^(API docs: /docs^)
echo   Health:   http://127.0.0.1:%BACKEND_PORT%/api/health
echo.
echo   Flow in the browser:
echo     Upload Video  -^>  Prepare  -^>  Analyze  -^>  Generate
echo     Explanation  -^>  Generate Narration  -^>  Create Final Video
echo.
echo   The app keeps running in the background. When you are done,
echo   double-click STOP_AI_VIDEO_EXPLAINER.bat.
echo.
pause
endlocal
