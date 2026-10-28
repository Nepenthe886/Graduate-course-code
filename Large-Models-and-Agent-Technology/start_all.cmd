@echo off
rem ============================================================
rem  Large-Models-and-Agent-Technology one-click launcher
rem
rem  Pass arguments straight through to start_all.py, e.g.
rem      start_all.cmd --status
rem      start_all.cmd --no-agent
rem      start_all.cmd --stop
rem      start_all.cmd --restart
rem
rem  NOTE: keep this file ASCII-only. cmd.exe re-reads batch lines
rem  after "chcp 65001" and non-ASCII bytes get mangled.
rem ============================================================
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
cd /d "%~dp0"
echo.
echo ============================================================
echo   Large-Models-and-Agent-Technology launcher
echo ============================================================
echo.
py -3.12 start_all.py %*
set "RC=%ERRORLEVEL%"
echo.
if "%RC%"=="0" (
  echo [OK] done / exit code 0
) else (
  echo [FAIL] exit code %RC% - see messages above
)
echo.
pause
exit /b %RC%
