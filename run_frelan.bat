@echo off
rem Interactive CLI run. Any arguments are passed straight through to main.py.
setlocal

call "%~dp0scripts\_env.bat"
if errorlevel 1 exit /b 1

%RUNNER% main.py %*

echo.
pause
endlocal
