@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

REM ---------------------------------------------------------------------------
REM  Sorpple web dashboard launcher.
REM
REM    start-sorpple-web.bat                 the bot + the dashboard
REM    start-sorpple-web.bat --no-bot        dashboard only
REM    start-sorpple-web.bat --port 8080     a different port
REM    start-sorpple-web.bat --rebuild       force a fresh front-end build
REM
REM  The bot is started too, because the dashboard's controls do nothing without
REM  it -- they only queue actions for the bot to apply.  An already-running bot
REM  is detected and left alone, so this never double-posts to Discord.
REM ---------------------------------------------------------------------------

set "PORT=7331"
set "NO_BOT="
set "REBUILD="

:parse
if "%~1"=="" goto parsed
if /i "%~1"=="--no-bot"   set "NO_BOT=1"  & shift & goto parse
if /i "%~1"=="--with-bot" shift & goto parse
if /i "%~1"=="--rebuild"  set "REBUILD=1" & shift & goto parse
if /i "%~1"=="--port"     set "PORT=%~2"  & shift & shift & goto parse
echo Unknown option: %~1
echo Usage: start-sorpple-web.bat [--no-bot] [--rebuild] [--port N]
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

REM -- Start the bot, unless one is already running or --no-bot was passed -----
REM  Without the bot, every control on the dashboard only queues an action that
REM  nothing will ever apply -- so it starts by default.
if not defined NO_BOT (
    "%PY%" sorpple_web.py --bot-running >nul 2>&1
    if errorlevel 1 (
        echo Starting the Discord bot in a separate window...
        start "Sorpple bot" cmd /k ""%PY%" sorpple.py"
        echo   Give it ~20 seconds to connect to Discord.
    ) else (
        echo Sorpple is already running; leaving it alone.
    )
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
