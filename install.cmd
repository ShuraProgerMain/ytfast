@echo off
rem YT Fast: install by double click. Uninstall: install.cmd -Uninstall
rem install.ps1 is read explicitly as UTF-8, otherwise Windows PowerShell 5 garbles Cyrillic.
powershell -NoProfile -ExecutionPolicy Bypass -Command "& ([scriptblock]::Create([IO.File]::ReadAllText('%~dp0install.ps1'))) -Source '%~dp0' %*"
echo.
pause
