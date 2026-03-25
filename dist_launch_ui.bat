@echo off
REM launch_ui.bat (dist version) — double-click to start the Codex-Aider Bridge UI
REM This file lives alongside bridge-app.exe in the dist\bridge-app\ folder.

title Codex-Aider Bridge UI

cd /d "%~dp0"

if not exist bridge-app.exe (
    echo.
    echo  ERROR: bridge-app.exe not found in this folder.
    echo  Make sure this file is in the same folder as bridge-app.exe.
    echo.
    pause
    exit /b 1
)

echo.
echo  Starting Codex-Aider Bridge UI...
echo  A browser window will open automatically at http://127.0.0.1:7823
echo  Press Ctrl+C in this window to stop the server.
echo.

bridge-app.exe %*

if errorlevel 1 (
    echo.
    echo  The UI server stopped unexpectedly (exit code %errorlevel%).
    pause
)
