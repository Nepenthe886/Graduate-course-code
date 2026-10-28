@echo off
rem ============================================================
rem  一键启动（A 方案）：模型实体 + LLM 代理 + UAV 代理
rem
rem  端口约定：
rem    llama-server (实体)   : 8012
rem    LLM 代理 (给 agent 用) : 8010  -> 8012   （规则注入 + 完成校验 + 强制委派）
rem    UAV 代理 (给 agent 用) : 8001  -> 8000   （天气改写 + 悬停起飞兜底）
rem
rem  agent 里需要填：
rem    LLM base_url : http://127.0.0.1:8010/v1
rem    UAV API      : http://127.0.0.1:8001
rem
rem  如果只想直连（不用代理）：scripts\run_server.cmd --direct
rem ============================================================
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0.."
set "PROJ=%CD%"
set "NAVDIR=%PROJ%\..\Location_amd_Navigation"

set "TMP=%PROJ%\.cache\temp"
set "TEMP=%PROJ%\.cache\temp"
set "PIP_CACHE_DIR=%PROJ%\.cache\pip"
set "HF_HOME=%PROJ%\.cache\hf"
set "MODELSCOPE_CACHE=%PROJ%\.cache\modelscope"
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"

set "LLM_UP=8012"
set "LLM_PUB=8010"
set "UAV_UP=8000"
set "UAV_PUB=8001"

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] 先运行 scripts\setup_env.cmd
    pause & exit /b 1
)
if not exist "%NAVDIR%\llm_adapter_proxy.py" (
    echo [ERROR] 找不到导航模块代理: %NAVDIR%\llm_adapter_proxy.py
    pause & exit /b 1
)

if /i "%~1"=="--direct" goto direct

echo ============================================================
echo   A 方案启动：模型实体 %LLM_UP% + LLM代理 %LLM_PUB% + UAV代理 %UAV_PUB%
echo ============================================================

rem ---- 端口预检 ----
netstat -ano | findstr /r /c:":%LLM_UP% .*LISTENING" >nul && (
    echo [ERROR] 端口 %LLM_UP% 已被占用，先关掉占用者
    pause & exit /b 1
)
netstat -ano | findstr /r /c:":%LLM_PUB% .*LISTENING" >nul && (
    echo [ERROR] 端口 %LLM_PUB% 已被占用，先关掉占用者
    pause & exit /b 1
)

rem ---- 1) 启动 llama-server 实体（新窗口） ----
echo [1/3] 启动 llama-server 实体 %LLM_UP% ...
start "Qwen3 llama-server :%LLM_UP%" /D "%PROJ%" "%PROJ%\.venv\Scripts\python.exe" -m src.server --port %LLM_UP%

echo        等待模型就绪（首次加载约 5-20 秒）...
"%PROJ%\.venv\Scripts\python.exe" "scripts\wait_health.py" 127.0.0.1 %LLM_UP% 180
if errorlevel 1 (
    echo [ERROR] 模型未就绪，检查 llama-server 窗口
    pause & exit /b 1
)

rem ---- 2) 启动 LLM 代理（新窗口） ----
echo [2/3] 启动 LLM 代理 %LLM_PUB% -^> %LLM_UP% ...
start "LLM proxy :%LLM_PUB%" /D "%NAVDIR%" "%NAVDIR%\run_llm_proxy.cmd"
"%PROJ%\.venv\Scripts\python.exe" "scripts\wait_health.py" 127.0.0.1 %LLM_PUB% 60
if errorlevel 1 (
    echo [WARN] LLM 代理未就绪，检查 LLM proxy 窗口
)

rem ---- 3) 若 UAV 后端在 8000，则启动 UAV 代理 ----
netstat -ano | findstr /r /c:":%UAV_UP% .*LISTENING" >nul
if errorlevel 1 (
    echo [3/3] [SKIP] UAV 后端 %UAV_UP% 未运行，稍后可单独运行 run_uav_proxy.cmd
) else (
    echo [3/3] 启动 UAV 代理 %UAV_PUB% -^> %UAV_UP% ...
    start "UAV proxy :%UAV_PUB%" /D "%NAVDIR%" "%NAVDIR%\run_uav_proxy.cmd"
)

echo.
echo ============================================================
echo  agent 设置（Settings）：
echo    LLM base_url : http://127.0.0.1:%LLM_PUB%/v1
echo    UAV API      : http://127.0.0.1:%UAV_PUB%
echo  然后点 Reload Agent
echo ============================================================
echo.
echo 关闭本窗口不会停止上面三个子窗口（各自独立）。
pause
exit /b 0

:direct
echo [direct] 直连模式：llama-server 直接占 %LLM_PUB%
"%PROJ%\.venv\Scripts\python.exe" -m src.server %*
set "RC=%ERRORLEVEL%"
echo.
echo [INFO] server exited with code %RC%
pause
exit /b %RC%
