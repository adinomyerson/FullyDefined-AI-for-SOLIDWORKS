@echo off
set "SETUP=%~dp0files\setup.ps1"
if not exist "%SETUP%" set "SETUP=%~dp0setup.ps1"
if not exist "%SETUP%" echo. & echo SolidWorks MCP was started from inside the zip file. & echo Right-click the zip, Extract All, then run Uninstall SolidWorks MCP from the extracted folder. & echo. & pause & exit /b 1
powershell -NoProfile -ExecutionPolicy Bypass -File "%SETUP%" -Uninstall %* & pause & (goto) 2>nul
