@echo off
REM launch_ui.bat — Double-click to start the Codex-Aider Bridge web UI
REM
REM Requires Python 3.10+ on PATH.  Flask is installed automatically if missing.

title Codex-Aider Bridge UI

REM Change to the directory this .bat lives in
cd /d "%~dp0"

REM Check that python is available
where python >nul 2>&1
if errorlevel 1 (
    echo.
    echo  ERROR: Python not found on PATH.
    echo  Please install Python 3.10+ from https://www.python.org/downloads/
    echo  and make sure "Add Python to PATH" is checked during install.
    echo.
    pause
    exit /b 1
)

echo.
echo  Starting Codex-Aider Bridge UI...
echo  A browser window will open automatically.
echo  Press Ctrl+C in this window to stop the server.
echo.

python launch_ui.py %*

REM Keep the window open if the script exits with an error
if errorlevel 1 (
    echo.
    echo  The UI server stopped unexpectedly (exit code %errorlevel%).
    pause
)
