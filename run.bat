@echo off
REM ──────────────────────────────────────────────────────────────
REM  AGENT ORION V2 — Windows Personal Assistant Launcher
REM  Usage:  run.bat              (default: CLI interface)
REM          run.bat telegram     (Telegram bot)
REM          run.bat slack        (Slack bot)
REM ──────────────────────────────────────────────────────────────

cd /d %~dp0

REM Activate the virtual environment
if exist .venv\Scripts\activate.bat (
    call .venv\Scripts\activate.bat
) else if exist ..\.venv\Scripts\activate.bat (
    call ..\.venv\Scripts\activate.bat
) else (
    echo ERROR: Virtual environment not found. Create one first:
    echo   python -m venv .venv
    echo   .venv\Scripts\pip install -r requirements.txt
    pause
    exit /b 1
)

REM Launch with the specified interface (default: cli)
set INTERFACE=%1
if "%INTERFACE%"=="" set INTERFACE=cli

echo ══════════════════════════════════════════════════════════
echo  AGENT ORION V2 — Multi-MCP + Checklist + Vision Validator
echo  Interface: %INTERFACE%
echo ══════════════════════════════════════════════════════════

python main.py --interface %INTERFACE%

pause
