@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

set "APP_NAME=LOMA Core Edition"
set "DIST_DIR=dist\%APP_NAME%"

echo %APP_NAME% — Windows build (onedir)
echo.

rem --- Reuse the same venv run.bat sets up; create it if this is the first build ---
if not exist "venv\Scripts\python.exe" (
    echo No venv found — run run.bat once first to set up dependencies, then re-run this script.
    pause
    exit /b 1
)

rem --- PyInstaller (build-only dependency, not needed to just run the app) ---
echo Checking PyInstaller...
venv\Scripts\python.exe -m pip show pyinstaller >nul 2>nul
if errorlevel 1 (
    echo Installing PyInstaller...
    venv\Scripts\python.exe -m pip install pyinstaller
    if errorlevel 1 (
        echo ERROR: Failed to install PyInstaller.
        pause
        exit /b 1
    )
)

rem --- Strip dev __pycache__ dirs from extensions/services before bundling — datas entries
rem     copy those trees verbatim with no filtering, so leftover .pyc caches from local dev
rem     runs otherwise ship too (pure bloat, never needed: Python recompiles at import time
rem     regardless). Knowledge Vault's deep nesting (extensions\knowledge_vault\
rem     retrieval\__pycache__\...) pushed some cached paths past 170 characters even from
rem     this repo's own location — combined with a longer install path on another machine,
rem     that's enough to exceed Windows' 260-char MAX_PATH during zip extraction and silently
rem     drop files, which is exactly what made the whole extension disappear on one PC while
rem     working fine on another.
echo Cleaning dev __pycache__ directories...
for /d /r extensions %%d in (__pycache__) do @if exist "%%d" rd /s /q "%%d"
for /d /r services %%d in (__pycache__) do @if exist "%%d" rd /s /q "%%d"

rem --- Build from the spec (onedir — NOT onefile: see packaging\loma_core.spec for why) ---
echo.
echo Building %APP_NAME% (this can take a few minutes)...
venv\Scripts\python.exe -m PyInstaller --noconfirm --clean packaging\loma_core.spec
if errorlevel 1 (
    echo ERROR: Build failed — see the output above.
    echo A "ModuleNotFoundError" on first build usually just means packaging\loma_core.spec
    echo needs that module added to hiddenimports or datas — not a broken build setup.
    pause
    exit /b 1
)

echo Bundling LICENSE.txt and README.txt with the app...
copy /Y LICENSE.txt "%DIST_DIR%\LICENSE.txt" >nul
copy /Y packaging\DIST_README.txt "%DIST_DIR%\README.txt" >nul

echo.
echo ============================================================
echo  Build complete: %DIST_DIR%\%APP_NAME%.exe
echo  (+ LICENSE.txt, README.txt, and an "Open Data Folder" shortcut
echo  that appears next to the exe the first time it's launched)
echo  Distribute by zipping the whole "%DIST_DIR%" folder — the exe
echo  only works together with the files next to it in that folder.
echo ============================================================
pause
