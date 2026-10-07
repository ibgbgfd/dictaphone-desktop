"""
Скрипт автоматического сквозного тестирования:
1. ПОЛУЧЕНИЕ данных по HTTP REST API (POST /start, прием data.json, валидация полей, POST /stop, защита от занятости BUSY 409).
2. ОТПРАВКА данных по HTTP POST (UploadWorker, multipart/form-data, отправка аудио и метаданных на вебхук).

Запуск:
    python test_e2e_pipeline.py
"""

import os
import sys
import time
import json
import threading
from pathlib import Path
import requests

# Настройка кодировки консоли для Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Импортируем модули проекта
from http_server import RESTServer
from uploader import UploadWorker
from test_webhook_server import WebhookHandler
from http.server import HTTPServer


WEBHOOK_PORT = 5679  # тестовый порт вебхука
REST_PORT = 8766     # тестовый порт REST API


def run_test(app):
    print("=" * 70)
    print("[TEST] НАЧАЛО ТЕСТИРОВАНИЯ: ОТПРАВКА И ПОЛУЧЕНИЕ ДАННЫХ (HTTP REST)")
    print("=" * 70)

    # ---------------------------------------------------------
    # ШАГ 1: Запуск локального HTTP Вебхук-сервера для теста ОТПРАВКИ
    # ---------------------------------------------------------
    print("\n[Этап 1] Запуск тестового HTTP вебхук-сервера (приемник данных)...")
    httpd = HTTPServer(("127.0.0.1", WEBHOOK_PORT), WebhookHandler)
    http_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    http_thread.start()
    print(f"  -> Вебхук-сервер готов на http://127.0.0.1:{WEBHOOK_PORT}/webhook-test/test")

    # ---------------------------------------------------------
    # ШАГ 2: Тестирование ОТПРАВКИ данных (UploadWorker)
    # ---------------------------------------------------------
    print("\n[Этап 2] Тестирование ОТПРАВКИ данных (HTTP POST multipart/form-data)...")
    test_audio_path = Path("test_sample_recording.webm")
    test_json_path = Path("test_sample_meta.json")

    # Создаем тестовые файлы
    test_audio_path.write_bytes(b"\x1a\x45\xdf\xa3" + b"TEST_AUDIO_STREAM_MOCK_DATA" * 50)
    meta_payload = {
        "id": "совещание_312",
        "title": "Обсуждение архитектуры проекта",
        "date": "28/09/2026 12:15",
        "participants": ["Иванов И.И.", "Петров П.П."],
        "agenda": [
            {"question": "Утверждение ТЗ", "assignee": "Сидоров С.С.", "position": "Ведущий программист"}
        ],
        "recording": {"duration": 15.5}
    }
    test_json_path.write_text(json.dumps(meta_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    upload_result = {"done": False, "success": False, "message": ""}
    
    def on_progress(rec_id, status):
        print(f"  [Uploader Progress] ID {rec_id}: {status}")

    def on_finished(rec_id, success, message):
        upload_result["done"] = True
        upload_result["success"] = success
        upload_result["message"] = message
        print(f"  [Uploader Finished] Успех: {success}, Сообщение: {message}")

    worker = UploadWorker(
        rec_id=999,
        audio_path=str(test_audio_path),
        json_path=str(test_json_path),
        upload_url=f"http://127.0.0.1:{WEBHOOK_PORT}/webhook-test/test",
        max_retries=2
    )
    worker.progress.connect(on_progress)
    worker.finished.connect(on_finished)

    worker.start()
    worker.wait(10000)
    app.processEvents()

    assert upload_result["success"], f"Ошибка отправки данных: {upload_result['message']}"
    print("  [OK] Отправка данных выполнена успешно! HTTP-сервер принял аудио и JSON.")

    # ---------------------------------------------------------
    # ШАГ 3: Тестирование ПОЛУЧЕНИЯ данных (HTTP REST Server)
    # ---------------------------------------------------------
    print("\n[Этап 3] Тестирование ПОЛУЧЕНИЯ данных (HTTP REST Server)...")
    received_session = {}
    stop_called = {"called": False}
    simulated_recording_state = {"is_recording": False}

    def on_session(data):
        print(f"  [REST Server Callback] Сервер получил сессию: id='{data.get('id')}', title='{data.get('title')}'")
        print(f"  [REST Server Callback] Участники: {data.get('participants')}")
        print(f"  [REST Server Callback] Вопросы повестки: {len(data.get('agenda', []))} шт.")
        received_session.update(data)
        simulated_recording_state["is_recording"] = True

    def on_stop():
        print("  [REST Server Callback] Получена команда остановки записи!")
        stop_called["called"] = True
        simulated_recording_state["is_recording"] = False

    rest_server = RESTServer(
        host="127.0.0.1",
        port=REST_PORT,
        on_session_received=on_session,
        on_stop_requested=on_stop,
        is_recording_func=lambda: simulated_recording_state["is_recording"]
    )
    assert rest_server.start(), "Не удалось запустить REST-сервер"
    print(f"  -> HTTP REST сервер слушает http://127.0.0.1:{REST_PORT}")

    base_url = f"http://127.0.0.1:{REST_PORT}"

    # 3.1. Тест GET /status
    print("  [REST Client] Проверка статуса GET /status...")
    status_resp = requests.get(f"{base_url}/status", timeout=2)
    assert status_resp.status_code == 200, f"Ошибка статуса: {status_resp.status_code}"
    print(f"  [REST Client] Статус до старта: {status_resp.json()}")

    # 3.2. Тест POST /start с data.json
    print("  [REST Client] Отправка POST /start с данными из data.json...")
    with open("data.json", "r", encoding="utf-8") as f:
        data_json = json.load(f)

    start_resp = requests.post(f"{base_url}/start", json=data_json, timeout=2)
    print(f"  [REST Client] Ответ на POST /start: HTTP {start_resp.status_code} {start_resp.text}")
    assert start_resp.status_code == 200, f"Ожидался HTTP 200, получен {start_resp.status_code}"
    assert received_session.get("id") == "совещание_312", "id сессии не совпадает"

    # 3.3. Тест повторного вызова во время записи (BUSY 409)
    print("  [REST Client] Проверка защиты от параллельного старта (BUSY 409)...")
    busy_resp = requests.post(f"{base_url}/start", json=data_json, timeout=2)
    print(f"  [REST Client] Ответ при занятости: HTTP {busy_resp.status_code} {busy_resp.text}")
    assert busy_resp.status_code == 409, f"Ожидался HTTP 409 Conflict, получен {busy_resp.status_code}"
    assert busy_resp.json().get("code") == "BUSY", "Ожидался код ошибки BUSY"

    # 3.4. Тест POST /stop
    print("  [REST Client] Отправка POST /stop...")
    stop_resp = requests.post(f"{base_url}/stop", timeout=2)
    print(f"  [REST Client] Ответ на POST /stop: HTTP {stop_resp.status_code} {stop_resp.text}")
    assert stop_resp.status_code == 200, f"Ожидался HTTP 200, получен {stop_resp.status_code}"
    assert stop_called["called"], "Колбэк остановки не был вызван"

    print("  [OK] Получение данных и управление через HTTP REST работают отлично!")

    # ---------------------------------------------------------
    # ЗАВЕРШЕНИЕ
    # ---------------------------------------------------------
    rest_server.stop()
    httpd.shutdown()

    # Удаляем временные файлы
    if test_audio_path.exists():
        test_audio_path.unlink()
    if test_json_path.exists():
        test_json_path.unlink()

    print("\n" + "=" * 70)
    print("[SUCCESS] ВСЕ ТЕСТЫ ПРОЙДЕНЫ УСПЕШНО!")
    print("   1. Прием данных (HTTP POST /start с JSON) - ОК")
    print("   2. Команда остановки (HTTP POST /stop) - ОК")
    print("   3. Защита от параллельных сессий (HTTP 409 BUSY) - ОК")
    print("   4. Отправка данных (HTTP POST multipart/form-data) - ОК")
    print("=" * 70 + "\n")


if __name__ == "__main__":
    from PyQt6.QtCore import QCoreApplication
    app = QCoreApplication(sys.argv)
    run_test(app)
