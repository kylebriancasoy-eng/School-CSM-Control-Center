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
echo   Optional app-owned DNS: UDP 53
echo.
echo The phone must connect to the laptop-created Mobile Hotspot.
echo Use the direct IPv4 address shown as verified in the Control Center.
echo A configured .home.arpa address must not be used unless the Control Center
echo says its app-owned DNS responder passed the local self-test. Windows hosts
echo entries affect this laptop only and do not prove that phones can resolve it.
echo The server health route is: http://RESPONDENT-IP:PORT/healthz
echo.
pause
