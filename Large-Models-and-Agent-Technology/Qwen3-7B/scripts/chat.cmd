@echo off
rem ============================================================
rem  命令行聊天客户端（需先启动 scripts\run_server.cmd）
rem  用法:
rem    scripts\chat.cmd
rem    scripts\chat.cmd --prompt "介绍一下 MoE" --stream
rem ============================================================
setlocal
chcp 65001 >nul
cd /d "%~dp0.."
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] .venv 不存在，先运行 scripts\setup_env.cmd
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -m src.cli %*
pause
exit /b %EXITCODE%
