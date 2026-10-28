@echo off
rem 检查是否有大文件/二进制被 git 跟踪（防止推到 GitHub）
setlocal
chcp 65001 >nul
cd /d "%~dp0.."
".venv\Scripts\python.exe" -m src.check_git
pause
exit /b %ERRORLEVEL%
