@echo off
REM build.bat — Build bridge-app.exe and (optionally) the installer
REM
REM Steps:
REM   1. Install PyInstaller + dependencies
REM   2. Build dist\bridge-app.exe  (single file, no _internal folder)
REM   3. If Inno Setup is found, build dist\CodexAiderBridgeSetup.exe
REM
REM Requirements on the BUILD machine:
REM   - Python 3.10+  on PATH
REM   - Inno Setup 6  (optional, for installer) https://jrsoftware.org/isinfo.php

title Building Codex-Aider Bridge

cd /d "%~dp0.."

REM ── 1. Check Python ──────────────────────────────────────────────────────────
where python >nul 2>&1
if errorlevel 1 (
    echo.
    echo  ERROR: Python not found on PATH.
    echo  Install Python 3.10+ from https://www.python.org/downloads/
    echo  and tick "Add Python to PATH" during setup.
    echo.
    pause & exit /b 1
)
for /f "tokens=*" %%v in ('python --version 2^>^&1') do echo  Python: %%v

REM ── 2. Install / upgrade build dependencies ──────────────────────────────────
echo.
echo  [1/3] Installing build dependencies...
python -m pip install --quiet --upgrade pyinstaller flask pywebview
if errorlevel 1 (
    echo  ERROR: pip install failed.
    pause & exit /b 1
)

REM ── 3. Clean ALL caches and previous build output ───────────────────────────
echo  [2/3] Cleaning caches and previous build artefacts...
if exist build          rmdir /s /q build
if exist dist\bridge-app.exe del /f /q dist\bridge-app.exe
REM Delete all __pycache__ folders so PyInstaller never uses stale .pyc files
for /d /r %%d in (__pycache__) do @if exist "%%d" rmdir /s /q "%%d" 2>nul
REM Delete PyInstaller spec cache
if exist __pycache__    rmdir /s /q __pycache__

REM ── 4. PyInstaller — single exe ─────────────────────────────────────────────
echo  [3/3] Building bridge-app.exe (this may take 1-3 minutes)...
echo.
python -m PyInstaller scripts\bridge.spec --clean --noconfirm
if errorlevel 1 (
    echo.
    echo  BUILD FAILED — see output above for details.
    pause & exit /b 1
)

echo.
echo  PyInstaller done: dist\bridge-app.exe

REM ── 5. Inno Setup — installer wizard (optional) ──────────────────────────────
echo.
set ISCC=
for %%p in (
    "C:\Program Files (x86)\Inno Setup 6\iscc.exe"
    "C:\Program Files\Inno Setup 6\iscc.exe"
    "C:\Program Files (x86)\Inno Setup 5\iscc.exe"
) do (
    if exist %%p set ISCC=%%~p
)

REM Also try iscc on PATH
where iscc >nul 2>&1 && set ISCC=iscc

if "%ISCC%"=="" (
    echo  Inno Setup not found — skipping installer build.
    echo  To build the installer, install Inno Setup 6 from:
    echo  https://jrsoftware.org/isinfo.php
    echo  then re-run build.bat.
) else (
    echo  Building installer with Inno Setup...
    "%ISCC%" scripts\installer.iss
    if errorlevel 1 (
        echo  WARNING: Inno Setup failed — exe build is still valid.
    ) else (
        echo  Installer done: dist\CodexAiderBridgeSetup.exe
    )
)

REM ── Done ─────────────────────────────────────────────────────────────────────
echo.
echo  ================================================================
echo   BUILD COMPLETE
echo  ================================================================
echo.
if exist dist\bridge-app.exe (
    echo   Single exe  : dist\bridge-app.exe
)
if exist dist\CodexAiderBridgeSetup.exe (
    echo   Installer   : dist\CodexAiderBridgeSetup.exe
)
echo.
echo   Distribute the installer to end users.
echo   The target machine needs aider, ollama, and codex/claude
echo   installed — those are external tools, not bundled in the exe.
echo  ================================================================
echo.
pause
