"""
Тестовый HTTP вебхук-сервер для проверки ОТПРАВКИ данных из приложения-диктофона.
Принимает multipart/form-data (аудиозапись .webm и JSON-метаданные),
логирует все поля и сохраняет полученные файлы в папку 'received_test_data/'.

Запуск:
    python test_webhook_server.py
    python test_webhook_server.py --port 5678
"""

import sys
import os
import json
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from email import message_from_bytes

# Настройка кодировки консоли для Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

PORT = 5678
SAVE_DIR = Path("received_test_data")


class WebhookHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Отключаем дефолтные логи BaseHTTPRequestHandler для чистоты вывода
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        resp = {"status": "ok", "message": "Тестовый вебхук-сервер работает. Ожидаются POST запросы."}
        self.wfile.write(json.dumps(resp, ensure_ascii=False).encode("utf-8"))

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        content_type = self.headers.get("Content-Type", "")

        print("\n" + "=" * 60)
        print(f"[Webhook] Получен входящий запрос: POST {self.path}")
        print(f"  Размер данных: {content_length} байт")
        print(f"  Content-Type: {content_type}")
        print("-" * 60)

        body = self.rfile.read(content_length)

        SAVE_DIR.mkdir(parents=True, exist_ok=True)
        form_data = {}
        files_saved = []

        try:
            # Парсинг multipart/form-data через стандартную библиотеку email
            headers_raw = f"Content-Type: {content_type}\r\n\r\n".encode("latin-1")
            msg = message_from_bytes(headers_raw + body)

            if msg.is_multipart():
                for part in msg.walk():
                    disposition = part.get("Content-Disposition", "")
                    if not disposition:
                        continue

                    field_name = part.get_param("name", header="content-disposition")
                    filename = part.get_filename()
                    payload = part.get_payload(decode=True)

                    if not payload:
                        continue

                    if filename:
                        # Сохраняем полученный файл (аудио или json)
                        safe_filename = Path(filename).name
                        out_path = SAVE_DIR / safe_filename
                        with open(out_path, "wb") as f_out:
                            f_out.write(payload)
                        files_saved.append({
                            "field": field_name,
                            "filename": safe_filename,
                            "size_bytes": len(payload),
                            "path": str(out_path.resolve())
                        })
                    elif field_name:
                        # Обычное текстовое поле формы
                        try:
                            val_str = payload.decode("utf-8")
                            form_data[field_name] = val_str
                        except UnicodeDecodeError:
                            form_data[field_name] = f"<{len(payload)} raw bytes>"
            else:
                # Если пришел простой body (JSON через curl или REST)
                try:
                    text_body = body.decode("utf-8")
                    try:
                        json_payload = json.loads(text_body)
                        if isinstance(json_payload, dict):
                            form_data.update(json_payload)
                            # Сохраняем полученный JSON файл
                            out_path = SAVE_DIR / "received_payload.json"
                            with open(out_path, "w", encoding="utf-8") as f_out:
                                f_out.write(json.dumps(json_payload, ensure_ascii=False, indent=2))
                            files_saved.append({
                                "field": "raw_json",
                                "filename": "received_payload.json",
                                "size_bytes": len(body),
                                "path": str(out_path.resolve())
                            })
                        else:
                            form_data["raw_json"] = json_payload
                    except json.JSONDecodeError:
                        form_data["raw_body"] = text_body
                except UnicodeDecodeError:
                    form_data["raw_body"] = f"<{len(body)} raw bytes>"

            # Выводим принятые поля метаданных
            print("[INFO] Принятые поля метаданных:")
            if form_data:
                for k, v in form_data.items():
                    display_val = v
                    if isinstance(v, (dict, list)):
                        display_val = json.dumps(v, ensure_ascii=False, indent=2)
                    elif isinstance(v, str) and (v.startswith("{") or v.startswith("[")):
                        try:
                            parsed = json.loads(v)
                            display_val = json.dumps(parsed, ensure_ascii=False, indent=2)
                        except Exception:
                            pass
                    print(f"  * {k}: {display_val}")
            else:
                print("  (текстовые поля отсутствуют)")

            # Выводим информацию о полученных файлах
            print("\n[FILES] Принятые файлы:")
            if files_saved:
                for f in files_saved:
                    size_kb = f["size_bytes"] / 1024.0
                    print(f"  * [{f['field']}] {f['filename']} ({size_kb:.1f} КБ) -> сохранен в: {f['path']}")
            else:
                print("  (файлы не обнаружены)")

            # Отправляем успешный ответ клиенту
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            response_data = {
                "status": "success",
                "code": 200,
                "message": "Данные успешно получены и обработаны вебхук-сервером",
                "files_count": len(files_saved),
                "fields_count": len(form_data)
            }
            self.wfile.write(json.dumps(response_data, ensure_ascii=False, indent=2).encode("utf-8"))
            print("[OK] Ответ 200 OK отправлен клиенту.")
            print("=" * 60 + "\n")

        except Exception as e:
            print(f"[ERROR] Ошибка обработки запроса: {e}")
            self.send_response(500)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            err_data = {"status": "error", "code": 500, "message": str(e)}
            self.wfile.write(json.dumps(err_data, ensure_ascii=False, indent=2).encode("utf-8"))


def run(port: int = PORT):
    server_address = ("0.0.0.0", port)
    httpd = HTTPServer(server_address, WebhookHandler)
    print("=" * 60)
    print(f"[START] Тестовый HTTP Вебхук-сервер запущен на порту {port}")
    print(f"   URL: http://127.0.0.1:{port}/webhook-test/test")
    print(f"   Файлы будут сохраняться в папку: {SAVE_DIR.resolve()}")
    print("   Ожидание входящих отправок (нажмите Ctrl+C для остановки)...")
    print("=" * 60)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[STOP] Тестовый вебхук-сервер остановлен.")
        httpd.server_close()


if __name__ == "__main__":
    p = PORT
    for i, arg in enumerate(sys.argv):
        if arg in ("--port", "-p") and i + 1 < len(sys.argv):
            p = int(sys.argv[i + 1])
    run(p)
