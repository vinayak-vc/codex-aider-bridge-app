@echo off
REM build.bat — Build bridge-app.exe with PyInstaller
REM
REM Requirements:
REM   - Python 3.10+ on PATH
REM   - pip install pyinstaller  (this script does it automatically)
REM
REM Output: dist\bridge-app\bridge-app.exe

title Building Codex-Aider Bridge App

cd /d "%~dp0"

REM ── Check Python ────────────────────────────────────────────────────────────
where python >nul 2>&1
if errorlevel 1 (
    echo.
    echo  ERROR: Python not found on PATH.
    echo  Install Python 3.10+ from https://www.python.org/downloads/
    echo  and tick "Add Python to PATH" during setup.
    echo.
    pause
    exit /b 1
)

REM ── Install / upgrade dependencies ──────────────────────────────────────────
echo.
echo  [1/3] Installing dependencies...
python -m pip install --quiet --upgrade pyinstaller flask
if errorlevel 1 (
    echo  ERROR: pip install failed.
    pause
    exit /b 1
)

REM ── Clean previous build ────────────────────────────────────────────────────
echo  [2/3] Cleaning previous build...
if exist build rmdir /s /q build
if exist dist\bridge-app rmdir /s /q dist\bridge-app

REM ── Run PyInstaller ─────────────────────────────────────────────────────────
echo  [3/3] Building executable...
echo.
python -m PyInstaller bridge.spec --clean --noconfirm
if errorlevel 1 (
    echo.
    echo  BUILD FAILED — see output above for details.
    pause
    exit /b 1
)

REM ── Copy launcher bat into dist ─────────────────────────────────────────────
copy /y launch_ui.bat dist\bridge-app\launch_ui.bat >nul

REM ── Done ────────────────────────────────────────────────────────────────────
echo.
echo  ============================================================
echo   BUILD COMPLETE
echo  ============================================================
echo   Executable : dist\bridge-app\bridge-app.exe
echo   Launcher   : dist\bridge-app\launch_ui.bat  (double-click)
echo.
echo   To distribute: copy the entire dist\bridge-app\ folder.
echo   aider, ollama, and codex/claude must be installed on the
echo   target machine — they are external tools, not bundled.
echo  ============================================================
echo.
pause
