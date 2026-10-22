@echo off
REM Double-click to start the death-gap replay coach.
REM Looks for a virtualenv in this folder (.venv, then venv).
REM Nothing hardcoded to one machine - works on any PC that followed the README.

cd /d "%~dp0"

set "PY="
if exist "%~dp0.venv\Scripts\python.exe" set "PY=%~dp0.venv\Scripts\python.exe"
if not defined PY if exist "%~dp0venv\Scripts\python.exe" set "PY=%~dp0venv\Scripts\python.exe"

if not defined PY (
    echo.
    echo [!] No virtualenv found next to run.bat.
    echo     Open a terminal in this folder and run:
    echo         python -m venv .venv
    echo         .venv\Scripts\python.exe -m pip install -r requirements.txt
    echo     Then put your API key in key.txt and double-click run.bat again.
    echo.
    pause
    exit /b 1
)

echo Using: %PY%
"%PY%" coach.py --analyze %*

pause
