@echo off
rem LLM 适配代理：8010(对外) -> 8012(模型实体)
rem 由 Qwen3-7B\scripts\run_server.cmd 调用；也可单独运行。
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
py -3.12 llm_adapter_proxy.py --port 8010 --upstream http://127.0.0.1:8012 --uav-base http://127.0.0.1:8001
echo.
echo [INFO] llm proxy exited with code %ERRORLEVEL%
pause
