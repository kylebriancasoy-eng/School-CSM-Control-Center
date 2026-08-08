@echo off
setlocal EnableExtensions
cd /d "%~dp0"
set "LOGDIR=%USERPROFILE%\Documents\MoSSLab Data\School CSM Control Center\logs"
if not exist "%LOGDIR%" mkdir "%LOGDIR%" >nul 2>&1
set "LOG=%LOGDIR%\launcher.log"

>>"%LOG%" echo.
>>"%LOG%" echo ----------------------------------------------------------------------------------------
>>"%LOG%" echo Launcher started: %DATE% %TIME%
>>"%LOG%" echo Working directory: %CD%
>>"%LOG%" echo Windows account: %USERNAME%
>>"%LOG%" echo Searching for a Python installation with all required packages...
>>"%LOG%" echo ----------------------------------------------------------------------------------------

where pythonw.exe >nul 2>&1
if not errorlevel 1 (
    pythonw.exe -c "import PySide6, qrcode, PIL, numpy, cv2" >nul 2>&1
    if not errorlevel 1 (
        >>"%LOG%" echo Launcher selected: pythonw.exe
        start "" /b pythonw.exe "%~dp0run_school_csm_control_center.py"
        exit /b 0
    )
    >>"%LOG%" echo pythonw.exe found, but one or more required packages are missing.
)

where pyw.exe >nul 2>&1
if not errorlevel 1 (
    pyw.exe -3 -c "import PySide6, qrcode, PIL, numpy, cv2" >nul 2>&1
    if not errorlevel 1 (
        >>"%LOG%" echo Launcher selected: pyw.exe -3
        start "" /b pyw.exe -3 "%~dp0run_school_csm_control_center.py"
        exit /b 0
    )
    >>"%LOG%" echo pyw.exe -3 found, but one or more required packages are missing.
)

where python.exe >nul 2>&1
if not errorlevel 1 (
    python.exe -c "import PySide6, qrcode, PIL, numpy, cv2" >nul 2>&1
    if not errorlevel 1 (
        >>"%LOG%" echo Launcher selected: python.exe
        start "" /b python.exe "%~dp0run_school_csm_control_center.py"
        exit /b 0
    )
    >>"%LOG%" echo python.exe found, but one or more required packages are missing.
)

where py.exe >nul 2>&1
if not errorlevel 1 (
    py.exe -3 -c "import PySide6, qrcode, PIL, numpy, cv2" >nul 2>&1
    if not errorlevel 1 (
        >>"%LOG%" echo Launcher selected: py.exe -3
        start "" /b py.exe -3 "%~dp0run_school_csm_control_center.py"
        exit /b 0
    )
    >>"%LOG%" echo py.exe -3 found, but one or more required packages are missing.
)

>>"%LOG%" echo ERROR: No usable Python installation with all required packages was found.
>>"%LOG%" echo Run INSTALL_REQUIREMENTS.cmd, then start the Control Center again.
msg * "School CSM Control Center could not find a Python installation with all required packages. Run INSTALL_REQUIREMENTS.cmd, then check MoSSLab Data\School CSM Control Center\logs."
exit /b 10
