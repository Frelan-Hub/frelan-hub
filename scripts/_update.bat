@echo off
rem Best-effort update, run just before the dashboard starts.
rem
rem Nothing here is allowed to be fatal. Being offline, having no git, or
rem working from a downloaded zip instead of a clone must never stop the app
rem from starting. Set AICB_NO_UPDATE=1 to skip it entirely.

if defined AICB_NO_UPDATE goto :eof

if not exist ".git" goto :eof
where git >nul 2>&1
if errorlevel 1 goto :eof

echo [AI-Conductor] Checking for updates...
git pull --ff-only

rem --ff-only means a dirty tree or a diverged branch fails cleanly and leaves
rem the checkout untouched, rather than starting a merge nobody asked for.
if errorlevel 1 (
    echo [AI-Conductor] Update skipped - local changes or a diverged branch.
    goto :eof
)

rem Cheap no-op unless the pull actually changed uv.lock. --inexact leaves
rem packages that are not in the lock alone, so a contributor's dev extras
rem survive an update run.
where uv >nul 2>&1
if not errorlevel 1 uv sync --quiet --inexact

goto :eof
