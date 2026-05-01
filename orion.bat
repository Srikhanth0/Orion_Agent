@echo off
setlocal

REM Usage: orion [run] [cli|telegram|slack]

set COMMAND=%1
set INTERFACE=%2

REM If first arg is "run", shift it to get the interface
if /i "%COMMAND%"=="run" (
    set INTERFACE=%2
) else (
    set INTERFACE=%1
)

REM Default to cli if no interface provided
if "%INTERFACE%"=="" set INTERFACE=cli

REM Find the project root (where this bat is)
set PROJECT_ROOT=%~dp0

echo [ORION] Launching %INTERFACE%...

REM Check for virtual environment and activate it
set VENV_PATH=
if exist "%PROJECT_ROOT%.venv\Scripts\activate.bat" set VENV_PATH="%PROJECT_ROOT%.venv\Scripts\activate.bat"

if not defined VENV_PATH (
    echo ERROR: Virtual environment not found - .venv
    exit /b 1
)

call %VENV_PATH%

REM Set PYTHONPATH to include root dir
set PYTHONPATH=%PROJECT_ROOT%;%PYTHONPATH%

REM Change to project root directory and run
cd /d "%PROJECT_ROOT%"
python main.py --interface %INTERFACE%

endlocal
