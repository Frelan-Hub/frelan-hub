@echo off
rem Interactive CLI run with Claude included as an equal third peer.
rem Any further arguments are passed straight through to main.py.
setlocal

call "%~dp0scripts\_env.bat"
if errorlevel 1 exit /b 1

%RUNNER% main.py --claude %*

echo.
pause
endlocal
