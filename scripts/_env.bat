@echo off
rem Resolve the repository root and the Python invocation used by every launcher.
rem
rem This file is meant to be `call`ed, and deliberately does NOT use setlocal:
rem both RUNNER and the working directory have to survive back into the caller.
rem
rem The working directory matters. The runtime resolves `main.py`, `missions\`,
rem `inputs\` and `outputs\` relative to it, so every launcher must start from
rem the repository root no matter where the shortcut was invoked from.

rem This file lives in scripts\, so the repository root is its parent.
cd /d "%~dp0.."

rem uv installs itself here and only edits PATH for *new* shells, so a first run
rem straight after install.bat would not otherwise see it.
if exist "%USERPROFILE%\.local\bin\uv.exe" set "PATH=%USERPROFILE%\.local\bin;%PATH%"

set "RUNNER="

rem Preferred: uv owns both the Python version and the locked dependencies.
where uv >nul 2>&1
if not errorlevel 1 set "RUNNER=uv run python"

rem Fallbacks, so a machine set up the manual way keeps working.
if not defined RUNNER if exist ".venv\Scripts\python.exe" set "RUNNER=.venv\Scripts\python.exe"
if not defined RUNNER (
    where python >nul 2>&1
    if not errorlevel 1 set "RUNNER=python"
)

if not defined RUNNER (
    echo.
    echo [AI-Conductor] No Python runtime was found.
    echo                Run install.bat once, then try again.
    echo.
    exit /b 1
)

exit /b 0
