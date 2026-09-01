@echo off
rem Start Chrome with the DevTools remote-debugging port the runtime connects to.
rem
rem This is your ordinary Chrome, driven over CDP - not an automated browser
rem profile. That is the point: it reuses the ChatGPT / Gemini / Claude sessions
rem you are already logged into.
setlocal

set "CDP_PORT=9223"
set "CHROME_PROFILE=%TEMP%\ai-conductor-b-chrome-profile"

rem A second Chrome cannot bind a port the first one already holds, and the
rem failure is silent, so check before launching rather than after.
netstat -ano | findstr /r /c:"LISTENING" | findstr /c:":%CDP_PORT% " >nul 2>&1
if not errorlevel 1 (
    echo [AI-Conductor] Port %CDP_PORT% is already listening.
    echo                A debug Chrome looks to be running - use that window.
    echo.
    pause
    exit /b 0
)

rem ProgramFiles(x86^) cannot be expanded inside a parenthesised block, so the
rem candidate paths are resolved into plain variables first.
set "P1=%ProgramFiles%\Google\Chrome\Application\chrome.exe"
set "P2=%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
set "P3=%LocalAppData%\Google\Chrome\Application\chrome.exe"

set "CHROME="
if exist "%P1%" set "CHROME=%P1%"
if not defined CHROME if exist "%P2%" set "CHROME=%P2%"
if not defined CHROME if exist "%P3%" set "CHROME=%P3%"

if not defined CHROME (
    echo.
    echo [AI-Conductor] Google Chrome was not found in any standard location.
    echo                Install Chrome, or start it manually with:
    echo                  chrome.exe --remote-debugging-port=%CDP_PORT%
    echo.
    pause
    exit /b 1
)

start "" "%CHROME%" --remote-debugging-port=%CDP_PORT% --user-data-dir="%CHROME_PROFILE%"

echo.
echo [AI-Conductor] Chrome started with remote debugging on port %CDP_PORT%.
echo.
echo   In THAT window, sign in to each peer you intend to use and leave a
echo   tab open for each one:
echo.
echo     - https://chatgpt.com
echo     - https://gemini.google.com
echo     - https://claude.ai        ^(only if you run with --claude^)
echo.
echo   The profile is kept at:
echo     %CHROME_PROFILE%
echo   so these sign-ins persist between sessions.
echo.
pause
endlocal
