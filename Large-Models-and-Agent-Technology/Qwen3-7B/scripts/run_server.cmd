@echo off
rem ============================================================
rem  启动 OpenAI 兼容服务（默认 configs/default.json）
rem  用法:
rem    scripts\run_server.cmd
rem    scripts\run_server.cmd --port 8011 --quant 8bit
rem    scripts\run_server.cmd --api-key sk-local
rem ============================================================
setlocal
chcp 65001 >nul
cd /d "%~dp0.."
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "HF_HOME=%CD%\.cache\hf"
set "HF_HUB_DISABLE_XET=1"

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] .venv 不存在，先运行 scripts\setup_env.cmd
    pause
    exit /b 1
)
if not exist "models\Qwen3-8B\config.json" (
    echo [WARN] 模型目录 models\Qwen3-8B 不完整，先运行 scripts\download_model.cmd
    echo.
)

".venv\Scripts\python.exe" -m src.server.app %*
set "RC=%ERRORLEVEL%"
echo.
echo [INFO] server exited with code %RC%
if not "%RC%"=="0" echo [HINT] 上面有报错信息，滚动查看。
pause
exit /b %RC%
