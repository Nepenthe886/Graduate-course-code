@echo off
rem ============================================================
rem  Pure model launcher: llama.cpp only
rem  No UAV proxy, no LLM proxy, no agent, no Controller.
rem
rem  Default endpoint : http://127.0.0.1:8010/v1  (OpenAI compatible)
rem  Stop             : Ctrl+C
rem  Override port    : scripts\run_model.cmd --port 8012
rem  Other params     : scripts\run_model.cmd --ctx 8192 --ngl 99
rem ============================================================
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0.."
set "PROJ=%CD%"

set "TMP=%PROJ%\.cache\temp"
set "TEMP=%PROJ%\.cache\temp"
set "PIP_CACHE_DIR=%PROJ%\.cache\pip"
set "HF_HOME=%PROJ%\.cache\hf"
set "MODELSCOPE_CACHE=%PROJ%\.cache\modelscope"
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] virtualenv not found. Run: scripts\setup_env.cmd
    pause
    exit /b 1
)
if not exist "runtime\llama\llama-server.exe" (
    echo [ERROR] llama-server.exe not found. Run: scripts\setup_runtime.cmd
    pause
    exit /b 1
)
if not exist "models\Qwen3-8B-Q4_K_M.gguf" (
    echo [ERROR] model not found. Run: scripts\download_model.cmd
    pause
    exit /b 1
)

echo ============================================================
echo   Local model only (llama.cpp, no proxies)
echo   Endpoint: http://127.0.0.1:8010/v1
echo   Stop    : Ctrl+C
echo ============================================================
echo.

".venv\Scripts\python.exe" -m src.server %*
set "RC=%ERRORLEVEL%"
echo.
echo [INFO] server exited with code %RC%
pause
exit /b %RC%
