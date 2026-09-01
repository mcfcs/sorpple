@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

REM ---------------------------------------------------------------------------
REM  Sorpple web dashboard launcher.
REM
REM    start-sorpple-web.bat                 dashboard only
REM    start-sorpple-web.bat --with-bot      dashboard + the Discord bot
REM    start-sorpple-web.bat --port 8080     a different port
REM    start-sorpple-web.bat --rebuild       force a fresh front-end build
REM
REM  The bot is NOT started by default: if sorpple.py is already running
REM  elsewhere, a second instance would post every listing to Discord twice.
REM ---------------------------------------------------------------------------

set "PORT=7331"
set "WITH_BOT="
set "REBUILD="

:parse
if "%~1"=="" goto parsed
if /i "%~1"=="--with-bot" set "WITH_BOT=1" & shift & goto parse
if /i "%~1"=="--rebuild"  set "REBUILD=1"  & shift & goto parse
if /i "%~1"=="--port"     set "PORT=%~2"   & shift & shift & goto parse
echo Unknown option: %~1
echo Usage: start-sorpple-web.bat [--with-bot] [--rebuild] [--port N]
exit /b 1
:parsed

REM -- Python: prefer the project venv, fall back to whatever is on PATH -------
set "PY=python"
if exist "venv\Scripts\python.exe" set "PY=venv\Scripts\python.exe"

"%PY%" --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python was not found.
    echo Install it from https://python.org, or create the venv this project expects.
    exit /b 1
)

REM -- Front end: build on first run, or when asked ---------------------------
if defined REBUILD (
    if exist "web\dist" rmdir /s /q "web\dist"
)

if not exist "web\dist\index.html" (
    echo Building the dashboard ^(first run^)...

    where npm >nul 2>&1
    if errorlevel 1 (
        echo ERROR: npm was not found, so the dashboard cannot be built.
        echo Install Node.js from https://nodejs.org and run this again.
        exit /b 1
    )

    if not exist "web\node_modules" (
        echo   Installing dependencies...
        pushd web
        call npm install --silent
        if errorlevel 1 ( popd & echo ERROR: npm install failed. & exit /b 1 )
        popd
    )

    pushd web
    call npm run build
    if errorlevel 1 ( popd & echo ERROR: the build failed. & exit /b 1 )
    popd
    echo   Built.
    echo.
)

REM -- Optionally start the bot in its own window -----------------------------
if defined WITH_BOT (
    echo Starting the Discord bot in a separate window...
    start "Sorpple bot" cmd /k ""%PY%" sorpple.py"
    echo.
)

REM -- Report the reachable addresses ----------------------------------------
echo  Sorpple dashboard
echo  ---------------------------------------------------------------
echo   Local      http://localhost:%PORT%
REM Ask Tailscale for this machine's name so the tailnet URL can be copied
REM straight out of the console.  Silent when Tailscale isn't installed.
for /f "usebackq delims=" %%I in (`"%PY%" sorpple_web.py --print-url --port %PORT% 2^>nul`) do echo   Tailnet    %%I
echo.
echo  Press Ctrl+C to stop.
echo  ---------------------------------------------------------------
echo.

REM Bound to 0.0.0.0 so other devices on the tailnet can reach it.  There is no
REM login on this server -- keep the port off the public internet.
"%PY%" sorpple_web.py --port %PORT% --host 0.0.0.0

endlocal
