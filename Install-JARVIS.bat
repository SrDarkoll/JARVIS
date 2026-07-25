@echo off
setlocal
cd /d "%~dp0"

echo ==========================================
echo       J.A.R.V.I.S. Windows Setup
echo ==========================================
echo.

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1" -CreateShortcut %*
if errorlevel 1 (
    echo.
    echo Installation failed. Review the message above.
    pause
    exit /b 1
)

echo.
echo Installation complete. Use Start-JARVIS.bat or the JARVIS shortcut.
pause
