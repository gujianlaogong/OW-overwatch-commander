@echo off
REM Double-click to start the death-gap replay coach.
REM Uses the managed Python that has dxcam / mss / openai installed.
REM Do NOT run with the system Python 3.12 - it has none of those packages.

cd /d "%~dp0"

"C:\Users\11834\.workbuddy\binaries\python\envs\default\Scripts\python.exe" coach.py --analyze %*

pause
