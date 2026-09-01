@echo off
rem Launch the control-plane dashboard, updating first.
setlocal

call "%~dp0scripts\_env.bat"
if errorlevel 1 exit /b 1

call "%~dp0scripts\_update.bat"

echo [AI-Conductor] Starting the dashboard - it will open automatically in
echo                your browser. Close this window to stop.
echo.

rem .streamlit\config.toml keeps headless = true so that a server started by
rem other means never grabs a browser tab. A launcher started by a human is
rem exactly the case that should, so it overrides the setting here - Streamlit
rem then opens the tab itself once the server is actually accepting requests.
rem
rem Leaving headless unmasks two first-run interruptions, both turned off in
rem .streamlit\config.toml rather than here: server.showEmailPrompt (Streamlit
rem asks for an email address) and logger.hideWelcomeMessage (the "install
rem Streamlit skills" nudge). hideWelcomeMessage also suppresses Streamlit's
rem printed Local/Network URL, which is why the banner above promises only the
rem browser tab - do not put a URL back into it. No port is pinned either: if
rem 8501 is taken, Streamlit picks the next free one and opens the tab there.
%RUNNER% -m streamlit run streamlit_app.py --server.headless=false

endlocal
