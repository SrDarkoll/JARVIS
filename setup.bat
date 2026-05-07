@echo off
echo Starting J.A.R.V.I.S. setup...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1" %*
pause
