@echo off
chcp 65001 > nul
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    start "" ".venv\Scripts\python.exe" main.py
) else (
    start "" python main.py
)
