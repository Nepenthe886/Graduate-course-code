@echo off
rem ============================================================
rem  一键运行：先启动本地模型（Qwen3-7B\scripts\run_model.cmd），
rem  就绪后调用 chat_llama.py 进行对话。仅影响本窗口，不改其他配置。
rem
rem  用法：
rem      run_chat.cmd
rem      run_chat.cmd --question "什么是智能体？"
rem      run_chat.cmd --stream
rem ============================================================
setlocal EnableExtensions
chcp 65001 >nul
set "HERE=%~dp0"
set "QWEN=%~dp0..\..\Qwen3-7B"
set "URL=http://127.0.0.1:8010/v1"

rem 1) 模型已在运行则跳过启动
powershell -NoProfile -Command "try{ $r=Invoke-WebRequest http://127.0.0.1:8010/health -TimeoutSec 2 -UseBasicParsing; if($r.StatusCode -eq 200){exit 0} }catch{}; exit 1" >nul 2>&1
if not errorlevel 1 (
    echo [INFO] 检测到模型服务已在运行，直接对话
    goto :chat
)

rem 2) 新窗口启动模型（只启动模型，不涉及代理/UAV）
if not exist "%QWEN%\scripts\run_model.cmd" (
    echo [ERROR] 未找到 %QWEN%\scripts\run_model.cmd
    pause
    exit /b 1
)
echo [1/2] 正在新窗口启动本地模型 8010 ...
start "Qwen3 model (8010)" /D "%QWEN%" cmd /k scripts\run_model.cmd

rem 3) 等待 /health 就绪（最多 180 秒，模型加载需要时间）
echo [2/2] 等待模型就绪 ...
powershell -NoProfile -Command "$deadline=(Get-Date).AddSeconds(180); while((Get-Date) -lt $deadline){ try{ $r=Invoke-WebRequest http://127.0.0.1:8010/health -TimeoutSec 2 -UseBasicParsing; if($r.StatusCode -eq 200){ exit 0 } }catch{}; Start-Sleep -Milliseconds 800 }; exit 1"
if errorlevel 1 (
    echo [ERROR] 模型 180 秒内未就绪，请查看模型窗口的报错
    pause
    exit /b 1
)
echo [OK] 模型已就绪：%URL%

:chat
py -3.12 "%HERE%chat_llama.py" %*
echo.
echo [INFO] 对话结束。模型仍在运行；如需停止，关闭 "Qwen3 model (8010)" 窗口即可。
pause
