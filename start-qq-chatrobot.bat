@echo off
setlocal
chcp 65001 >nul

pushd "%~dp0"
if errorlevel 1 (
    echo [启动失败] 无法进入项目目录："%~dp0"
    pause
    exit /b 1
)

if not exist "%~dp0scripts\start_all.ps1" (
    echo [启动失败] 缺少 scripts\start_all.ps1。
    popd
    pause
    exit /b 1
)

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_all.ps1"
set "START_RESULT=%ERRORLEVEL%"
popd

if not "%START_RESULT%"=="0" (
    echo.
    echo [启动失败] 一键启动返回错误码 %START_RESULT%。日志位置："%~dp0data\logs" 和 "%~dp0data\astrbot\logs"。
)

pause
exit /b %START_RESULT%
