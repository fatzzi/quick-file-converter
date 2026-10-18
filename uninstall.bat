@echo off
rem Stops ShiftDrop and removes it from Windows startup. Leaves this folder alone.
call "%~dp0stop.bat"
del "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\ShiftDrop.lnk" 2>nul
echo ShiftDrop removed from startup. Delete this folder to remove it completely.
pause
