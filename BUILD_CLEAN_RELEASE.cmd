@echo off
setlocal
cd /d "%~dp0"
py -3 tools\build_release.py
if errorlevel 1 (
  echo.
  echo The clean release package could not be built.
  pause
  exit /b 1
)
echo.
echo Clean release archive and SHA-256 manifest created in the parent dist folder.
pause
