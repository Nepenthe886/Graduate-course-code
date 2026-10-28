@echo off
rem 下载 GGUF 模型到 models\（仅下载到项目内）
setlocal
chcp 65001 >nul
cd /d "%~dp0.."
set "TMP=%CD%\.cache\temp"
set "TEMP=%CD%\.cache\temp"
set "MODELSCOPE_CACHE=%CD%\.cache\modelscope"
if not exist "%TMP%" mkdir "%TMP%"
if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] 先运行 scripts\setup_env.cmd
    pause & exit /b 1
)
".venv\Scripts\python.exe" -m src.download %*
set "RC=%ERRORLEVEL%"
echo.
pause
exit /b %RC%
