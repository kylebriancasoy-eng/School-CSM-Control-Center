@echo off
cd /d "%~dp0"
if exist "%~dp0START_SCHOOL_CSM_CONTROL_CENTER.vbs" (
    start "" /b wscript.exe //nologo "%~dp0START_SCHOOL_CSM_CONTROL_CENTER.vbs"
    exit /b 0
)
call "%~dp0START_SCHOOL_CSM_CONTROL_CENTER_INTERNAL.cmd"
if errorlevel 1 pause
