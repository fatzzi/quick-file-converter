@echo off
rem ShiftDrop setup: creates .venv, installs the Python packages, and optionally
rem installs ffmpeg and adds ShiftDrop to Windows startup.
rem   install.bat          interactive
rem   install.bat /quiet   packages only, no questions, does not start the app
setlocal
cd /d "%~dp0"
set QUIET=
if /i "%~1"=="/quiet" set QUIET=1

set PY=
where py >nul 2>nul && set "PY=py -3"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY (
    echo Python 3 was not found. Install it from https://www.python.org/downloads/
    echo and tick "Add python.exe to PATH", then run this again.
    if not defined QUIET pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment...
    %PY% -m venv .venv || goto :fail
)
echo Installing packages...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements.txt || goto :fail
".venv\Scripts\python.exe" -c "import PIL, pillow_heif, tkinterdnd2" || goto :fail
echo Packages OK.

if defined QUIET goto :done

where ffmpeg >nul 2>nul
if errorlevel 1 (
    echo.
    echo ffmpeg is not installed. It is only needed for video and audio files.
    choice /c YN /m "Install ffmpeg now with winget"
    if not errorlevel 2 winget install --id Gyan.FFmpeg -e --accept-package-agreements
)

echo.
choice /c YN /m "Start ShiftDrop automatically when you sign in to Windows"
if not errorlevel 2 (
    powershell -NoProfile -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Startup')+'\ShiftDrop.lnk'); $s.TargetPath='%~dp0.venv\Scripts\pythonw.exe'; $s.Arguments='\"%~dp0shiftdrop.py\"'; $s.WorkingDirectory='%~dp0'; $s.Save()"
    echo Added to startup.
)

call "%~dp0start.bat"
echo.
echo ShiftDrop is running. Shift-drag a file in File Explorer to try it.
pause

:done
exit /b 0

:fail
echo Setup failed - see the messages above.
if not defined QUIET pause
exit /b 1
