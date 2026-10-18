@echo off
rem Starts ShiftDrop in the background (no console window).
if not exist "%~dp0.venv\Scripts\pythonw.exe" (
    echo ShiftDrop is not set up yet - run install.bat first.
    pause
    exit /b 1
)
start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0shiftdrop.py"
