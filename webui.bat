@echo off
setlocal EnableExtensions
cd /d "%~dp0"
chcp 65001 >nul 2>&1

set "CMD=%~1"
if "%CMD%"=="" set "CMD=start"

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0webui.ps1" %CMD% %2 %3 %4 %5 %6 %7 %8 %9
set "ERR=%ERRORLEVEL%"

rem 双击运行（无参数）时暂停，避免窗口一闪而过
if "%~1"=="" (
    echo.
    pause
)

exit /b %ERR%
