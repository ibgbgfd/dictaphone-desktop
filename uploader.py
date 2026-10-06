import os
import json
import time
from pathlib import Path
from typing import Optional
import requests
import urllib3
from PyQt6.QtCore import QThread, pyqtSignal

# Подавляем предупреждения при работе с локальными самоподписанными HTTPS-сертификатами
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class UploadWorker(QThread):
    progress = pyqtSignal(int, str)       # rec_id, status_text
    finished = pyqtSignal(int, bool, str) # rec_id, success, message

    def __init__(
        self,
        rec_id: int,
        audio_path: str,
        json_path: str,
        upload_url: str,
        max_retries: int = 5,
        verify_ssl: bool = False
    ):
        super().__init__()
        self.rec_id = rec_id
        self.audio_path = audio_path
        self.json_path = json_path
        self.max_retries = max(1, max_retries)
        self.verify_ssl = verify_ssl

        # Автоматическое определение и нормализация протокола (HTTP / HTTPS)
        raw_url = upload_url.strip()
        if not raw_url.startswith("http://") and not raw_url.startswith("https://"):
            # Если протокол не указан явно:
            if ":443" in raw_url or "https" in raw_url.lower():
                self.upload_url = "https://" + raw_url
            else:
                self.upload_url = "http://" + raw_url
        else:
            self.upload_url = raw_url

        self.is_https = self.upload_url.lower().startswith("https://")

    def run(self):
        protocol_tag = "HTTPS" if self.is_https else "HTTP"
        print(f"[Uploader] Старт выгрузки на {self.upload_url} [Протокол: {protocol_tag}]")

        if not self.upload_url or not self.upload_url.startswith("http"):
            self.finished.emit(self.rec_id, False, "Не задан корректный URL для отправки")
            return

        audio_file = Path(self.audio_path)
        json_file = Path(self.json_path)

        if not audio_file.exists():
            self.finished.emit(self.rec_id, False, f"Аудиофайл не найден: {audio_file.name}")
            return

        json_content = ""
        json_data = {}
        if json_file.exists():
            try:
                with open(json_file, "r", encoding="utf-8") as f:
                    json_content = f.read()
                    json_data = json.loads(json_content)
            except Exception as e:
                print(f"[Uploader] Ошибка чтения json файла: {e}")

        last_error = ""
        for attempt in range(1, self.max_retries + 1):
            try:
                self.progress.emit(self.rec_id, f"Отправка {protocol_tag} ({attempt}/{self.max_retries})...")
                
                with open(audio_file, "rb") as fa:
                    files = {
                        "audio": (audio_file.name, fa, "audio/webm"),
                        "json": (json_file.name, json_content.encode("utf-8"), "application/json")
                    }
                    data = {
                        "id": str(json_data.get("id", "")),
                        "session_id": str(json_data.get("id", "")),
                        "title": str(json_data.get("title", "")),
                        "date": str(json_data.get("date", "")),
                        "participants": json.dumps(json_data.get("participants", []), ensure_ascii=False)
                            if isinstance(json_data.get("participants"), list)
                            else str(json_data.get("participants", "")),
                        "agenda": json.dumps(json_data.get("agenda", []), ensure_ascii=False)
                            if isinstance(json_data.get("agenda"), list)
                            else str(json_data.get("agenda", "")),
                        "duration": str(json_data.get("recording", {}).get("duration", 0)),
                        "file_size": str(audio_file.stat().st_size),
                        "metadata": json_content
                    }
                    
                    try:
                        # Первая попытка с заданным режимом проверки SSL
                        response = requests.post(
                            self.upload_url,
                            files=files,
                            data=data,
                            timeout=35,
                            verify=self.verify_ssl
                        )
                    except requests.exceptions.SSLError as ssl_err:
                        # Умная обработка HTTPS: если сертификат самоподписанный, автоматически повторяем без verify
                        print(f"[Uploader] Обнаружен самоподписанный SSL сертификат: {ssl_err}. Повтор в безопасном нестрогом режиме...")
                        self.progress.emit(self.rec_id, f"HTTPS (самоподписанный SSL, попытка {attempt})...")
                        fa.seek(0)
                        files["audio"] = (audio_file.name, fa, "audio/webm")
                        response = requests.post(
                            self.upload_url,
                            files=files,
                            data=data,
                            timeout=35,
                            verify=False
                        )
                    
                    if 200 <= response.status_code < 300:
                        self.finished.emit(self.rec_id, True, f"Успешно отправлено ({protocol_tag})")
                        return
                    else:
                        last_error = f"HTTP {response.status_code}: {response.text[:100]}"
            except Exception as e:
                last_error = str(e)
                print(f"[Uploader] Попытка {attempt}/{self.max_retries} завершилась с ошибкой: {e}")

            if attempt < self.max_retries:
                # Нарастающий интервал ожидания: 2с, 4с, 6с, 8с...
                backoff = 2.0 * attempt
                time.sleep(backoff)

        self.finished.emit(self.rec_id, False, f"Ошибка сети ({protocol_tag}): {last_error}")
