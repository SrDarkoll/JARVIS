@echo off
setlocal
cd /d "%~dp0"

if not exist "%~dp0venv\Scripts\python.exe" (
    echo JARVIS is not installed in this folder.
    echo Run Install-JARVIS.bat first.
    pause
    exit /b 1
)

"%~dp0venv\Scripts\python.exe" "%~dp0start_app.py"
set "JARVIS_EXIT_CODE=%ERRORLEVEL%"

if not "%JARVIS_EXIT_CODE%"=="0" (
    echo.
    echo JARVIS exited with code %JARVIS_EXIT_CODE%.
    pause
)

exit /b %JARVIS_EXIT_CODE%
