@echo off
rem Starts Quick File Converter in the background (no console window).
if not exist "%~dp0.venv\Scripts\pythonw.exe" (
    echo Quick File Converter is not set up yet - run install.bat first.
    pause
    exit /b 1
)
start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0quick_file_converter.py"
