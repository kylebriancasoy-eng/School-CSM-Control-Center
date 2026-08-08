@echo off
setlocal
echo SCHOOL CSM CONTROL CENTER - NETWORK DIAGNOSTICS
echo =================================================
echo.
echo IPv4 addresses on this laptop:
ipconfig | findstr /i /c:"IPv4"
echo.
echo Listening survey and captive-portal TCP ports:
netstat -ano -p tcp | findstr /r /c:":80 .*LISTENING" /c:":8080 .*LISTENING" /c:":53 .*LISTENING"
echo.
echo Captive-portal DNS UDP listener:
netstat -ano -p udp | findstr /r /c:":53 "
echo.
echo Expected default arrangement:
echo   Survey Form server: TCP 8080
echo   Captive Portal redirect: TCP 80
echo   Captive Portal DNS: UDP 53, or Windows probe-host fallback
echo.
echo The phone must connect to the laptop-created Mobile Hotspot.
echo Use the hotspot adapter IPv4 address shown in the Control Center.
echo.
pause
