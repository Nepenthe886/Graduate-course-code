@echo off
rem ============================================================
rem  一次性环境搭建：用 PIM 的 py -3.12 创建 .venv 并安装依赖
rem  可重复执行（已存在 .venv 时只补装依赖）
rem ============================================================
setlocal
chcp 65001 >nul
cd /d "%~dp0.."

set "PY=.venv\Scripts\python.exe"
set "PYPI=-i https://mirrors.ustc.edu.cn/pypi/simple"
set "TORCH_INDEX=--index-url https://download.pytorch.org/whl/cu128"

echo [1/4] 检查 Python 3.12 ^(PIM^) ...
py -3.12 -V
if errorlevel 1 (
    echo [ERROR] py -3.12 不可用。请先在 PIM 里安装 3.12： py install 3.12
    goto :err
)

if not exist ".venv\Scripts\python.exe" (
    echo [2/4] 创建 .venv ...
    py -3.12 -m venv .venv
    if errorlevel 1 goto :err
) else (
    echo [2/4] .venv 已存在，跳过创建
)

echo [3/4] 升级 pip ...
"%PY%" -m pip install --upgrade pip %PYPI%
if errorlevel 1 goto :err

echo [4/4] 安装依赖 ...
echo        4.1 CUDA 版 torch（约 2.8 GB，必须用 cu128 主索引）
"%PY%" -m pip install torch %TORCH_INDEX%
if errorlevel 1 goto :err
echo        4.2 其余依赖
"%PY%" -m pip install -r requirements.txt %PYPI%
if errorlevel 1 goto :err

echo.
echo ============================================================
echo 验证 GPU：
"%PY%" -c "import torch;print(torch.__version__, torch.version.cuda, torch.cuda.is_available(), torch.cuda.get_device_capability())"
echo 期望输出: 2.x.x+cu128 12.8 True (12, 0)
echo ============================================================
echo 下一步： scripts\download_model.cmd
pause
exit /b 0

:err
echo.
echo [ERROR] 出错了，请看上面的输出。
pause
exit /b 1
