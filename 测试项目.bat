@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo [1/4] 检查 Python 环境和开发依赖...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\bootstrap_windows.ps1" -IncludeDev
if errorlevel 1 goto :fail

echo [2/4] 运行 pytest...
"%~dp0.venv\Scripts\python.exe" -m pytest -q
if errorlevel 1 goto :fail

echo [3/4] 运行 ruff...
"%~dp0.venv\Scripts\python.exe" -m ruff check app tests
if errorlevel 1 goto :fail

echo [4/4] 运行 compileall...
"%~dp0.venv\Scripts\python.exe" -m compileall -q app tests
if errorlevel 1 goto :fail

echo.
echo ===== 全部检查通过 =====
pause
exit /b 0

:fail
echo.
echo ===== 检查失败，请把本窗口最后的报错发给 ChatGPT =====
pause
exit /b 1
