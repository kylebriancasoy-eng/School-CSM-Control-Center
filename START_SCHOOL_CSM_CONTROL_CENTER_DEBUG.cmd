@echo off
setlocal EnableExtensions
cd /d "%~dp0"
echo School CSM Control Center visible debug launcher
echo Diagnostic output is written under Documents\MoSSLab Data\School CSM Control Center\logs.
echo.

where python.exe >nul 2>&1
if not errorlevel 1 (
    python.exe -c "import PySide6, qrcode, PIL, numpy, cv2" >nul 2>&1
    if not errorlevel 1 (
        echo Using python.exe
        python.exe "%~dp0run_school_csm_control_center.py"
        set "EXITCODE=%ERRORLEVEL%"
        goto :done
    )
)

where py.exe >nul 2>&1
if not errorlevel 1 (
    py.exe -3 -c "import PySide6, qrcode, PIL, numpy, cv2" >nul 2>&1
    if not errorlevel 1 (
        echo Using py.exe -3
        py.exe -3 "%~dp0run_school_csm_control_center.py"
        set "EXITCODE=%ERRORLEVEL%"
        goto :done
    )
)

echo ERROR: A Python installation with all required packages was not found.
echo Run INSTALL_REQUIREMENTS.cmd first.
set "EXITCODE=10"

:done
echo.
echo Process exit code: %EXITCODE%
echo Open Documents\MoSSLab Data\School CSM Control Center\logs\control-center.log for diagnosis.
pause
exit /b %EXITCODE%
