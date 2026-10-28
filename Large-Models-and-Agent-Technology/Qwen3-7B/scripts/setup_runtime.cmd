@echo off
rem 下载 llama.cpp Windows CUDA 预编译包到 runtime\llama\
setlocal
chcp 65001 >nul
cd /d "%~dp0.."
set "TMP=%CD%\.cache\temp"
set "TEMP=%CD%\.cache\temp"
if not exist "%TMP%" mkdir "%TMP%"
if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] 先运行 scripts\setup_env.cmd
    pause & exit /b 1
)
".venv\Scripts\python.exe" -m src.runtime %*
set "RC=%ERRORLEVEL%"
echo.
pause
exit /b %RC%
