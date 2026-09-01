@echo off
rem One-shot installer. Double-click it; nothing needs to be installed first.
rem
rem uv is the reason this can be one click: it is a single binary that manages
rem the Python version as well as the dependencies, so "install Python first" -
rem the step most people give up on - is not asked of anyone.
rem
rem Pass "dev" (install.bat dev) to include the test dependencies.
setlocal

cd /d "%~dp0"

echo.
echo ==================================================================
echo    AI-Conductor B Runtime - installer
echo ==================================================================
echo.

rem ---------------------------------------------------------- 1. uv -- rem
if exist "%USERPROFILE%\.local\bin\uv.exe" set "PATH=%USERPROFILE%\.local\bin;%PATH%"

where uv >nul 2>&1
if errorlevel 1 (
    echo [1/5] Installing uv...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
    set "PATH=%USERPROFILE%\.local\bin;%PATH%"
) else (
    echo [1/5] uv is already installed.
)

where uv >nul 2>&1
if errorlevel 1 (
    echo.
    echo   ERROR: uv could not be installed automatically.
    echo          Install it by hand from https://docs.astral.sh/uv/ and
    echo          run this script again.
    goto :fail
)

rem --------------------------------------------------------- 2. git -- rem
echo [2/5] Checking for Git...
where git >nul 2>&1
if errorlevel 1 (
    where winget >nul 2>&1
    if errorlevel 1 (
        echo       Git not found, and winget is unavailable to install it.
        echo       The app will work, but updates will have to be manual.
    ) else (
        echo       Installing Git, so the app can update itself...
        winget install --id Git.Git -e --source winget --accept-package-agreements --accept-source-agreements
    )
) else (
    echo       Git is present - updates will run automatically.
)

rem ------------------------------------------------- 3. dependencies -- rem
echo [3/5] Installing Python and the locked dependencies...
if /i "%~1"=="dev" (
    uv sync --extra dev
) else (
    uv sync
)
if errorlevel 1 goto :fail

rem -------------------------------------------------- 4. playwright -- rem
echo [4/5] Installing the Playwright Chromium driver...
uv run playwright install chromium
if errorlevel 1 goto :fail

rem --------------------------------------------------- 5. shortcuts -- rem
echo [5/5] Creating Desktop and Start Menu shortcuts...
powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\new-shortcuts.ps1" -Root "%CD%"

echo.
echo ==================================================================
echo    Installed.
echo ==================================================================
echo.
echo   Start the dashboard from the "AI-Conductor B" shortcut on your
echo   Desktop, or by running run_ui.bat here. It checks for updates
echo   each time it starts.
echo.
echo   Before your first run, sign the browser in to the AI peers:
echo.
echo     1. Run launch_chrome_debug.bat
echo     2. In the Chrome window it opens, sign in to chatgpt.com,
echo        gemini.google.com, and claude.ai, leaving a tab open on each
echo     3. Start the dashboard
echo.
pause
endlocal
exit /b 0

:fail
echo.
echo   Installation failed. Nothing was left half-configured - fix the
echo   error above and run install.bat again.
echo.
pause
endlocal
exit /b 1
