@echo off
rem UAV 适配代理：8001(对外) -> 8000(UAV 后端)
rem 天气改写为 clear；悬停状态下的 take_off 自动转 change_altitude。
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
py -3.12 uav_adapter_proxy.py --port 8001 --upstream http://127.0.0.1:8000
echo.
echo [INFO] uav proxy exited with code %ERRORLEVEL%
pause
