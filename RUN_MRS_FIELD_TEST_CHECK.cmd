@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 run_mrs_field_test_check.py
) else (
  python run_mrs_field_test_check.py
)
echo.
pause
