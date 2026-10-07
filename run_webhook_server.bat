@echo off
chcp 65001 > nul
cd /d "%~dp0"
echo ========================================================
echo   Запуск тестового Вебхук-сервера приема данных (HTTP)
echo ========================================================
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" test_webhook_server.py
) else (
    python test_webhook_server.py
)
pause
