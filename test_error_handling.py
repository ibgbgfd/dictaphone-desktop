"""
Скрипт автоматического тестирования обработки ошибок валидации и исключительных ситуаций.
Проверяет:
1. Валидатор сессий session_validator (все сценарии неправильного JSON, отсутствие id/title, пустые поля, синонимы, типы).
2. HTTP REST сервер (ответы 400, 413, 422, 404, 405, 409, 200 на различные ошибки клиента).
"""

import sys
import json
import time
import requests
from pathlib import Path

# Настройка кодировки консоли
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from session_validator import validate_session_data, EXAMPLE_SESSION_PAYLOAD
from http_server import RESTServer


def test_validator_unit():
    print("=" * 60)
    print("[1] ТЕСТИРОВАНИЕ ВАЛИДАТОРА СЕССИЙ (UNIT TESTS)")
    print("=" * 60)

    # 1.1. Не словарь (массив)
    ok, err, norm, missing = validate_session_data([{"id": "1"}])
    assert not ok, "Ожидалась ошибка для списка"
    assert "должен быть объектом" in err
    print("  [OK] Неверный тип корня (список) отсечен")

    # 1.2. Пустой объект {}
    ok, err, norm, missing = validate_session_data({})
    assert not ok, "Ожидалась ошибка для {}"
    assert "id" in missing and "title" in missing
    assert "Передан пустой JSON объект" in err
    print("  [OK] Пустой объект {} отсечен с кодом отсутствующих полей")

    # 1.3. Отсутствует id
    ok, err, norm, missing = validate_session_data({"title": "Планерка"})
    assert not ok, "Ожидалась ошибка при отсутствии id"
    assert "id" in missing and "title" not in missing
    print("  [OK] Отсутствие 'id' корректно обнаружено")

    # 1.4. Пустой id (пробелы)
    ok, err, norm, missing = validate_session_data({"id": "   ", "title": "Планерка"})
    assert not ok, "Ожидалась ошибка для пустого id"
    assert "id" in missing
    print("  [OK] Пустой 'id' (пробелы) отсечен")

    # 1.5. Отсутствует title (название конференции)
    ok, err, norm, missing = validate_session_data({"id": "rec_001"})
    assert not ok, "Ожидалась ошибка при отсутствии title"
    assert "title" in missing and "id" not in missing
    print("  [OK] Отсутствие 'title' корректно обнаружено")

    # 1.6. Пустой title (пробелы)
    ok, err, norm, missing = validate_session_data({"id": "rec_001", "title": "   "})
    assert not ok, "Ожидалась ошибка для пустого title"
    assert "title" in missing
    print("  [OK] Пустой 'title' (пробелы) отсечен")

    # 1.7. Некорректный тип id (словарь)
    ok, err, norm, missing = validate_session_data({"id": {"nested": 1}, "title": "Планерка"})
    assert not ok
    assert "Поле 'id' должно быть строковым или числовым" in err
    print("  [OK] Некорректный тип для id отсечен")

    # 1.8. Некорректный тип title (список)
    ok, err, norm, missing = validate_session_data({"id": "rec_001", "title": ["Список", "тем"]})
    assert not ok
    assert "Поле 'title' (название конференции) должно быть строкой" in err
    print("  [OK] Некорректный тип для title отсечен")

    # 1.9. Поддержка дружелюбного синонима 'conference'/'name'
    ok, err, norm, missing = validate_session_data({"id": "rec_001", "conference": "Совет директоров"})
    assert ok, f"Ожидался успех для синонима: {err}"
    assert norm["title"] == "Совет директоров"
    print("  [OK] Синоним 'conference' успешно принят как название")

    # 1.10. Валидный эталонный пакет
    with open("data.json", "r", encoding="utf-8") as f:
        data = json.load(f)
    ok, err, norm, missing = validate_session_data(data)
    assert ok, f"Ошибка на data.json: {err}"
    assert norm["id"] == "совещание_312"
    assert norm["title"] == "Обсуждение архитектуры проекта"
    assert len(norm["participants"]) == 2
    assert len(norm["agenda"]) == 2
    assert norm["agenda"][0]["question"] == "Утверждение ТЗ на разработку аудиорекордера"
    print("  [OK] Эталонный пакет data.json валидирован и нормализован успешно!")
    print("\n[V] ВСЕ UNIT-ТЕСТЫ ВАЛИДАТОРА УСПЕШНО ПРОЙДЕНЫ!\n")


def test_http_error_responses():
    print("=" * 60)
    print("[2] ТЕСТИРОВАНИЕ HTTP REST API НА ОБРАБОТКУ ОШИБОК КЛИЕНТА")
    print("=" * 60)

    test_port = 8799
    base_url = f"http://127.0.0.1:{test_port}"
    active_rec_state = {"is_recording": False}

    received_sessions = []

    server = RESTServer(
        host="127.0.0.1",
        port=test_port,
        on_session_received=lambda s: received_sessions.append(s),
        is_recording_func=lambda: active_rec_state["is_recording"]
    )
    assert server.start(), "Не удалось запустить тестовый сервер"

    try:
        # 2.1. Пустое тело POST /start (Content-Length: 0)
        resp = requests.post(f"{base_url}/start", data="", headers={"Content-Type": "application/json"})
        assert resp.status_code == 400
        j = resp.json()
        assert j["error"] == "EMPTY_BODY"
        print("  [OK] Тест 400 EMPTY_BODY пройден")

        # 2.2. Синтаксически битый JSON
        resp = requests.post(f"{base_url}/start", data='{"id": 12, "title": ', headers={"Content-Type": "application/json"})
        assert resp.status_code == 400
        j = resp.json()
        assert j["error"] == "INVALID_JSON_SYNTAX"
        assert "line" in j and "column" in j
        print(f"  [OK] Тест 400 INVALID_JSON_SYNTAX пройден (строка {j['line']}, колонка {j['column']})")

        # 2.3. Корневой элемент JSON не объект (массив)
        resp = requests.post(f"{base_url}/start", json=[{"id": "1"}])
        assert resp.status_code == 400
        j = resp.json()
        assert j["error"] == "INVALID_JSON_STRUCTURE"
        print("  [OK] Тест 400 INVALID_JSON_STRUCTURE пройден")

        # 2.4. Пустой объект {} -> 422
        resp = requests.post(f"{base_url}/start", json={})
        assert resp.status_code == 422
        j = resp.json()
        assert j["error"] == "EMPTY_JSON_OBJECT"
        assert "id" in j["missing_fields"] and "title" in j["missing_fields"]
        print("  [OK] Тест 422 EMPTY_JSON_OBJECT пройден")

        # 2.5. Отсутствие id -> 400 MISSING_REQUIRED_FIELDS
        resp = requests.post(f"{base_url}/start", json={"title": "Совещание отдела"})
        assert resp.status_code == 400
        j = resp.json()
        assert j["error"] == "MISSING_REQUIRED_FIELDS"
        assert "id" in j["missing_fields"]
        print("  [OK] Тест 400 MISSING_REQUIRED_FIELDS (нет id) пройден")

        # 2.6. Отсутствие title -> 400 MISSING_REQUIRED_FIELDS
        resp = requests.post(f"{base_url}/start", json={"id": "совещание_404"})
        assert resp.status_code == 400
        j = resp.json()
        assert j["error"] == "MISSING_REQUIRED_FIELDS"
        assert "title" in j["missing_fields"]
        print("  [OK] Тест 400 MISSING_REQUIRED_FIELDS (нет title) пройден")

        # 2.7. Неизвестный эндпоинт POST /random
        resp = requests.post(f"{base_url}/random", json={"id": "1", "title": "Test"})
        assert resp.status_code == 404
        assert resp.json()["error"] == "NOT_FOUND"
        print("  [OK] Тест 404 NOT_FOUND пройден")

        # 2.8. Неподдерживаемый метод PUT /start
        resp = requests.put(f"{base_url}/start", json={"id": "1", "title": "Test"})
        assert resp.status_code == 405
        assert resp.json()["error"] == "METHOD_NOT_ALLOWED"
        print("  [OK] Тест 405 METHOD_NOT_ALLOWED пройден")

        # 2.9. Успешный старт сессии -> 200 OK
        with open("data.json", "r", encoding="utf-8") as f:
            valid_payload = json.load(f)
        resp = requests.post(f"{base_url}/start", json=valid_payload)
        assert resp.status_code == 200
        j = resp.json()
        assert j["status"] == "ok"
        assert j["id"] == "совещание_312"
        assert len(received_sessions) == 1
        print("  [OK] Тест 200 OK (валидный JSON) пройден")

        # 2.10. Параллельный запрос при активной записи -> 409 BUSY
        active_rec_state["is_recording"] = True
        resp = requests.post(f"{base_url}/start", json=valid_payload)
        assert resp.status_code == 409
        j = resp.json()
        assert j["error"] == "RECORDER_BUSY"
        print("  [OK] Тест 409 RECORDER_BUSY пройден")

        # 2.11. Остановка записи POST /stop
        resp = requests.post(f"{base_url}/stop")
        assert resp.status_code == 200
        j = resp.json()
        assert j["status"] == "ok"
        active_rec_state["is_recording"] = False
        print("  [OK] Тест 200 OK POST /stop пройден")

        # 2.12. Повторная остановка когда запись не идет
        resp = requests.post(f"{base_url}/stop")
        assert resp.status_code == 200
        j = resp.json()
        assert j["is_recording"] is False
        assert "не была активна" in j["message"]
        print("  [OK] Тест остановки в режиме ожидания пройден")

        print("\n[V] ВСЕ HTTP REST ТЕСТЫ ОБРАБОТКИ ОШИБОК УСПЕШНО ПРОЙДЕНЫ!\n")

    finally:
        server.stop()


if __name__ == "__main__":
    test_validator_unit()
    test_http_error_responses()
