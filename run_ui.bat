@echo off
rem Launch the control-plane dashboard, updating first.
setlocal

call "%~dp0scripts\_env.bat"
if errorlevel 1 exit /b 1

call "%~dp0scripts\_update.bat"

echo [AI-Conductor] Starting the dashboard at http://localhost:8501
echo                Close this window to stop it.
echo.

rem .streamlit\config.toml keeps headless = true so that a server started by
rem other means never grabs a browser tab. A launcher started by a human is
rem exactly the case that should, so it overrides the setting here - Streamlit
rem then opens the tab itself once the server is actually accepting requests.
%RUNNER% -m streamlit run streamlit_app.py --server.headless=false

endlocal
