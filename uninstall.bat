@echo off
rem Stops Quick File Converter and removes it from Windows startup. Leaves this folder alone.
call "%~dp0stop.bat"
del "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Quick File Converter.lnk" 2>nul
rem shortcut name used before the rename
del "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\ShiftDrop.lnk" 2>nul
echo Quick File Converter removed from startup. Delete this folder to remove it completely.
pause
