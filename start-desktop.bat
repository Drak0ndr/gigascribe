@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Missing local virtual environment. See README.md
    pause
    exit /b 1
)
".venv\Scripts\python.exe" desktop.py
if errorlevel 1 pause
