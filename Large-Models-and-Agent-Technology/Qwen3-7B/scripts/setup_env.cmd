@echo off
rem ============================================================
rem  1) 用 PIM 的 py -3.12 建 .venv
rem  2) 安装轻量依赖（无 torch）
rem  所有缓存/临时文件都锁在项目 .cache 内，不写 C 盘
rem ============================================================
setlocal
chcp 65001 >nul
cd /d "%~dp0.."

set "PROJ=%CD%"
set "TMP=%PROJ%\.cache\temp"
set "TEMP=%PROJ%\.cache\temp"
set "PIP_CACHE_DIR=%PROJ%\.cache\pip"
set "MODELSCOPE_CACHE=%PROJ%\.cache\modelscope"
set "HF_HOME=%PROJ%\.cache\hf"
set "HF_HUB_DISABLE_XET=1"
set "PYTHONPYCACHEPREFIX=%PROJ%\.cache\pycache"
if not exist "%TMP%" mkdir "%TMP%"
if not exist "%PIP_CACHE_DIR%" mkdir "%PIP_CACHE_DIR%"
if not exist "%PYTHONPYCACHEPREFIX%" mkdir "%PYTHONPYCACHEPREFIX%"

echo [1/3] 检查 Python 3.12（PIM）...
py -3.12 -V
if errorlevel 1 (
    echo [ERROR] py -3.12 不可用。先执行: py install 3.12
    goto :err
)

echo [2/3] 创建 .venv ...
if not exist ".venv\Scripts\python.exe" (
    py -3.12 -m venv .venv
    if errorlevel 1 goto :err
) else (
    echo       已存在，跳过
)

echo [3/3] 安装依赖（缓存: .cache\pip）...
".venv\Scripts\python.exe" -m pip install --upgrade pip --cache-dir "%PIP_CACHE_DIR%" -i https://mirrors.aliyun.com/pypi/simple --timeout 120 --retries 5
".venv\Scripts\python.exe" -m pip install -r requirements.txt --cache-dir "%PIP_CACHE_DIR%" -i https://mirrors.aliyun.com/pypi/simple --timeout 120 --retries 5
if errorlevel 1 goto :err

echo.
echo ============================================================
".venv\Scripts\python.exe" -V
echo 下一步: scripts\setup_runtime.cmd
echo ============================================================
pause
exit /b 0

:err
echo.
echo [ERROR] 出错了，看上面的输出。
pause
exit /b 1
