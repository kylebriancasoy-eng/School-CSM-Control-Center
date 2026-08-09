@echo off
setlocal EnableExtensions

set "PREFERRED_PORT=%~1"
set "FALLBACK_PORT=%~2"
set "CAPTIVE_PORTAL=%~3"
if not defined PREFERRED_PORT set "PREFERRED_PORT=8080"
if not defined FALLBACK_PORT set "FALLBACK_PORT=8080"
if not defined CAPTIVE_PORTAL set "CAPTIVE_PORTAL=0"

net session >nul 2>&1
if not %errorlevel%==0 (
  echo Requesting Administrator permission to add Windows Firewall rules...
  powershell -NoProfile -ExecutionPolicy Bypass -Command "$p = Start-Process -FilePath '%~f0' -ArgumentList '%PREFERRED_PORT%','%FALLBACK_PORT%','%CAPTIVE_PORTAL%' -Verb RunAs -Wait -PassThru; exit $p.ExitCode"
  exit /b %errorlevel%
)

echo Configuring Windows Firewall for School CSM Control Center...

rem Remove legacy broad rules before installing only the currently required
rem Private-network and LocalSubnet rules.
call :DELETE_RULE TCP 80
call :DELETE_RULE TCP 8080
call :DELETE_RULE TCP 53
call :DELETE_RULE UDP 53

call :ADD_TCP_RULE %PREFERRED_PORT%
if errorlevel 1 exit /b 1
call :ADD_TCP_RULE %FALLBACK_PORT%
if errorlevel 1 exit /b 1

if "%CAPTIVE_PORTAL%"=="1" (
  call :ADD_TCP_RULE 80
  if errorlevel 1 exit /b 1
  rem Captive Portal DNS interception is optional. Do not fail normal server
  rem startup if Windows reserves or rejects the DNS rule.
  call :ADD_UDP_RULE 53
)

echo Firewall access configured for local-subnet respondent devices.
exit /b 0

:ADD_TCP_RULE
set "PORT=%~1"
if not defined PORT exit /b 0
set "RULE=School CSM Control Center TCP %PORT%"
netsh advfirewall firewall delete rule name="%RULE%" >nul 2>&1
netsh advfirewall firewall add rule name="%RULE%" dir=in action=allow protocol=TCP localport=%PORT% profile=private remoteip=localsubnet >nul
if errorlevel 1 exit /b 1
exit /b 0

:ADD_UDP_RULE
set "PORT=%~1"
if not defined PORT exit /b 0
set "RULE=School CSM Control Center UDP %PORT%"
netsh advfirewall firewall delete rule name="%RULE%" >nul 2>&1
netsh advfirewall firewall add rule name="%RULE%" dir=in action=allow protocol=UDP localport=%PORT% profile=private remoteip=localsubnet >nul
exit /b 0

:DELETE_RULE
set "PROTOCOL=%~1"
set "PORT=%~2"
if not defined PORT exit /b 0
set "RULE=School CSM Control Center %PROTOCOL% %PORT%"
netsh advfirewall firewall delete rule name="%RULE%" >nul 2>&1
exit /b 0
