@echo off
rem ============================================================
rem  下载模型权重（默认：hf-mirror + 关闭 Xet）
rem  用法:
rem    scripts\download_model.cmd
rem    scripts\download_model.cmd --method modelscope
rem    scripts\download_model.cmd --mirror --repo Qwen/Qwen3-4B --out models/Qwen3-4B
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

".venv\Scripts\python.exe" -m src.download %*
set "RC=%ERRORLEVEL%"
echo.
if not "%RC%"=="0" echo [HINT] 下载失败可改用魔搭: scripts\download_model.cmd --method modelscope
pause
exit /b %RC%
