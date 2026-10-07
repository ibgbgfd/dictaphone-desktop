@echo off
chcp 65001 > nul
cd /d "%~dp0"
echo ========================================================
echo   Запуск сквозного теста отправки и получения данных
echo ========================================================
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" test_error_handling.py
    echo.
    ".venv\Scripts\python.exe" test_e2e_pipeline.py
) else (
    python test_error_handling.py
    echo.
    python test_e2e_pipeline.py
)
pause
