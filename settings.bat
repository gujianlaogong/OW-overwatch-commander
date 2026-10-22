@echo off
REM Double-click to open the settings window (calls, prompt, voice).
REM Same virtualenv lookup as run.bat - nothing hardcoded to one machine.

cd /d "%~dp0"

set "PY="
if exist "%~dp0.venv\Scripts\python.exe" set "PY=%~dp0.venv\Scripts\python.exe"
if not defined PY if exist "%~dp0venv\Scripts\python.exe" set "PY=%~dp0venv\Scripts\python.exe"

if not defined PY (
    echo.
    echo [!] No virtualenv found next to settings.bat.
    echo     Open a terminal in this folder and run:
    echo         python -m venv .venv
    echo         .venv\Scripts\python.exe -m pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)

"%PY%" settings.py
