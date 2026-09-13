@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo LOMA launcher
echo.

rem --- Find a Python 3.10-3.12 interpreter -------------------------------------
set "PYTHON_CMD="

where py >nul 2>nul
if %errorlevel%==0 (
    for %%V in (3.12 3.11 3.10) do (
        if not defined PYTHON_CMD (
            py -%%V --version >nul 2>nul
            if !errorlevel!==0 set "PYTHON_CMD=py -%%V"
        )
    )
)

if not defined PYTHON_CMD (
    where python >nul 2>nul
    if !errorlevel!==0 set "PYTHON_CMD=python"
)

if not defined PYTHON_CMD (
    echo ERROR: Python was not found on this machine.
    echo LOMA needs Python 3.10-3.12: https://www.python.org/downloads/
    echo During install, check "Add python.exe to PATH".
    pause
    exit /b 1
)

for /f "tokens=*" %%V in ('%PYTHON_CMD% -c "import sys; print(sys.version_info[0], sys.version_info[1])" 2^>nul') do set "PY_VER=%%V"
for /f "tokens=1,2" %%A in ("%PY_VER%") do (
    set "PY_MAJOR=%%A"
    set "PY_MINOR=%%B"
)

set "PY_OK="
if "%PY_MAJOR%"=="3" (
    if %PY_MINOR% geq 10 if %PY_MINOR% leq 12 set "PY_OK=1"
)

if not defined PY_OK (
    echo ERROR: Found Python %PY_MAJOR%.%PY_MINOR%, but LOMA needs 3.10-3.12.
    echo Install a supported version from https://www.python.org/downloads/
    echo then run this script again.
    pause
    exit /b 1
)

echo Using %PYTHON_CMD% (Python %PY_MAJOR%.%PY_MINOR%)

rem --- Create the virtual environment if missing -------------------------------
if not exist "venv\Scripts\python.exe" (
    echo Creating virtual environment in .\venv ...
    %PYTHON_CMD% -m venv venv
    if not exist "venv\Scripts\python.exe" (
        echo ERROR: Failed to create the virtual environment.
        pause
        exit /b 1
    )
)

rem --- Install/update core dependencies (fast no-op if already satisfied) ------
echo Checking dependencies (first run downloads ~1-2 GB, this can take a while)...
venv\Scripts\python.exe -m pip install --upgrade pip >nul
venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 (
    echo ERROR: Dependency install failed - see the output above.
    pause
    exit /b 1
)

rem --- Launch --------------------------------------------------------------
echo.
echo Starting LOMA...
venv\Scripts\python.exe main.py
echo.
echo LOMA has stopped.
pause
