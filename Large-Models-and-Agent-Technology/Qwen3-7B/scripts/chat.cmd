@echo off
rem 命令行对话（需先启动 scripts\run_server.cmd）
setlocal
chcp 65001 >nul
cd /d "%~dp0.."
if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] 先运行 scripts\setup_env.cmd
    pause & exit /b 1
)
".venv\Scripts\python.exe" -m src.cli %*
pause
exit /b %ERRORLEVEL%
