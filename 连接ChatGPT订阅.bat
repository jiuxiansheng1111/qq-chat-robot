@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "TASK_CHATGPT_PYTHON=%~dp0data\astrbot\runtime\tool-envs\astrbot\Scripts\python.exe"
if not exist "%TASK_CHATGPT_PYTHON%" set "TASK_CHATGPT_PYTHON=%~dp0.venv\Scripts\python.exe"
"%TASK_CHATGPT_PYTHON%" scripts\connect_chatgpt.py login --activate --open-browser
pause
