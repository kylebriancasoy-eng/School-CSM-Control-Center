@echo off
setlocal EnableExtensions
cd /d "%~dp0"

where py.exe >nul 2>&1
if not errorlevel 1 (
    py.exe -3 -m pip install --upgrade pip
    py.exe -3 -m pip install -r requirements-lock.txt
    goto :done
)

where python.exe >nul 2>&1
if not errorlevel 1 (
    python.exe -m pip install --upgrade pip
    python.exe -m pip install -r requirements-lock.txt
    goto :done
)

echo Python 3 was not found. Install Python 3.11 or newer first.

:done
pause
