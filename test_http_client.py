"""
Скрипт для тестирования HTTP REST API диктофона.

Использование:
    python test_http_client.py           # Отправить совещание из data.json и остановить через 5 сек
    python test_http_client.py 10        # Отправить совещание и остановить через 10 сек
    python test_http_client.py --stop    # Отправить команду остановки записи
    python test_http_client.py --status  # Проверить статус диктофона

Эквиваленты в curl:
    Старт:
        curl.exe -X POST "http://127.0.0.1:8765/start" -H "Content-Type: application/json; charset=utf-8" -d @data.json
    Стоп:
        curl.exe -X POST "http://127.0.0.1:8765/stop"
    Статус:
        curl.exe "http://127.0.0.1:8765/status"
"""

import sys
import json
import time
from pathlib import Path
import requests

# Настройка кодировки консоли для Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

DEFAULT_URL = "http://127.0.0.1:8765"


def send_start(url: str = DEFAULT_URL, data_file: str = "data.json", auto_stop_sec: int = 5):
    p = Path(data_file)
    if not p.exists():
        print(f"[ERROR] Файл {data_file} не найден!")
        return

    with open(p, "r", encoding="utf-8") as f:
        payload = json.load(f)

    start_url = f"{url.rstrip('/')}/start"
    print(f"[Client] Отправка POST {start_url} с данными из {data_file}...")
    try:
        resp = requests.post(
            start_url,
            json=payload,
            headers={"Content-Type": "application/json; charset=utf-8"},
            timeout=5
        )
        print(f"[Server Response] HTTP {resp.status_code}: {resp.text}")

        if resp.status_code == 200 and auto_stop_sec > 0:
            print(f"[Client] Запись идет... Ожидание {auto_stop_sec} сек перед остановкой...")
            time.sleep(auto_stop_sec)
            send_stop(url)
    except Exception as e:
        print(f"[Client Error] Ошибка запроса к серверу: {e}")


def send_stop(url: str = DEFAULT_URL):
    stop_url = f"{url.rstrip('/')}/stop"
    print(f"[Client] Отправка POST {stop_url}...")
    try:
        resp = requests.post(stop_url, timeout=5)
        print(f"[Server Response] HTTP {resp.status_code}: {resp.text}")
    except Exception as e:
        print(f"[Client Error] {e}")


def check_status(url: str = DEFAULT_URL):
    status_url = f"{url.rstrip('/')}/status"
    print(f"[Client] Запрос GET {status_url}...")
    try:
        resp = requests.get(status_url, timeout=5)
        print(f"[Server Response] HTTP {resp.status_code}: {resp.text}")
    except Exception as e:
        print(f"[Client Error] {e}")


if __name__ == "__main__":
    if "--stop" in sys.argv:
        send_stop()
    elif "--status" in sys.argv:
        check_status()
    else:
        sec = 5
        for arg in sys.argv[1:]:
            if arg.isdigit():
                sec = int(arg)
        send_start(auto_stop_sec=sec)
