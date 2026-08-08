@echo off
setlocal EnableExtensions

net session >nul 2>&1
if not %errorlevel%==0 (
  powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~f0' -Verb RunAs -Wait"
  exit /b %errorlevel%
)

set "HOSTS=%SystemRoot%\System32\drivers\etc\hosts"
set "TEMPFILE=%TEMP%\school_csm_hosts_%RANDOM%.tmp"

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$p='%HOSTS%'; $b='# BEGIN SCHOOL CSM CAPTIVE PORTAL'; $e='# END SCHOOL CSM CAPTIVE PORTAL'; $inside=$false; $out=@(); foreach($line in Get-Content -LiteralPath $p){ if($line.Trim() -eq $b){$inside=$true; continue}; if($line.Trim() -eq $e){$inside=$false; continue}; if(-not $inside){$out += $line} }; Set-Content -LiteralPath $p -Value $out -Encoding ascii"

ipconfig /flushdns >nul 2>&1
echo School CSM captive-portal probe mappings were removed.
pause
