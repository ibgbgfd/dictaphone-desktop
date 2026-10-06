import json
import threading
from typing import Callable, Optional, Dict, Any
from http.server import HTTPServer, ThreadingHTTPServer, BaseHTTPRequestHandler


class DictaphoneHTTPHandler(BaseHTTPRequestHandler):
    server: 'HTTPServerWrapper'

    def log_message(self, format, *args):
        # Отключаем дефолтный шум в консоли
        pass

    def _send_json_response(self, status_code: int, data: Dict[str, Any]):
        data["status_code"] = status_code
        if "code" not in data:
            data["code"] = status_code
        response_bytes = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(response_bytes)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()
        self.wfile.write(response_bytes)

    def do_OPTIONS(self):
        # Поддержка CORS preflight запросов
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

    def do_GET(self):
        normalized_path = self.path.split("?")[0].rstrip("/").lower()
        if not normalized_path:
            normalized_path = "/"

        if normalized_path in ("/", "/api"):
            self._send_json_response(200, {
                "status": "ok",
                "code": 200,
                "service": "Диктофон REST API",
                "endpoints": {
                    "POST /start": "Запуск записи (передача JSON метаданных совещания)",
                    "POST /stop": "Остановка текущей записи",
                    "GET /status": "Проверка текущего статуса рекордера",
                    "POST /show": "Разворачивание окна диктофона из трея"
                }
            })
            return

        if normalized_path in ("/status", "/api/status"):
            is_rec = self.server.wrapper.is_recording_func()
            self._send_json_response(200, {
                "status": "ok",
                "code": 200,
                "is_recording": is_rec,
                "message": "Идет запись" if is_rec else "Ожидание сессии"
            })
            return

        if normalized_path in ("/show", "/api/show"):
            if self.server.wrapper.on_show_requested:
                self.server.wrapper.on_show_requested()
            self._send_json_response(200, {
                "status": "ok",
                "code": 200,
                "message": "Окно диктофона развернуто на передний план"
            })
            return

        self._send_json_response(404, {
            "status": "error",
            "code": 404,
            "error": "NOT_FOUND",
            "message": f"Эндпоинт {self.path} не найден. Используйте /start, /stop или /status"
        })

    def do_POST(self):
        try:
            normalized_path = self.path.split("?")[0].rstrip("/").lower()
            if not normalized_path:
                normalized_path = "/"

            # 1. Быстрая проверка команд остановки записи и показа окна по URL (не требуют обязательного тела)
            if normalized_path in ("/stop", "/api/stop"):
                if self.server.wrapper.on_stop_requested:
                    self.server.wrapper.on_stop_requested()
                print("[REST API] Получена команда остановки записи (POST /stop)")
                self._send_json_response(200, {
                    "status": "ok",
                    "code": 200,
                    "message": "Команда остановки записи успешно выполнена"
                })
                return

            if normalized_path in ("/show", "/api/show"):
                if self.server.wrapper.on_show_requested:
                    self.server.wrapper.on_show_requested()
                print("[REST API] Получена команда разворачивания окна (POST /show)")
                self._send_json_response(200, {
                    "status": "ok",
                    "code": 200,
                    "message": "Окно диктофона развернуто на передний план"
                })
                return

            # Для передачи сессии (/start) требуется тело запроса с JSON
            content_length = int(self.headers.get("Content-Length", 0))
            if content_length <= 0:
                self._send_json_response(400, {
                    "status": "error",
                    "code": 400,
                    "error": "EMPTY_BODY",
                    "message": "Тело запроса пустое. Отправьте JSON с метаданными сессии (id, title)."
                })
                return

            body = self.rfile.read(content_length)
            if not body or not body.strip():
                self._send_json_response(400, {
                    "status": "error",
                    "code": 400,
                    "error": "EMPTY_BODY",
                    "message": "Тело запроса пустое. Отправьте JSON с метаданными сессии (id, title)."
                })
                return

            # Разбор тела запроса
            try:
                data = json.loads(body.decode("utf-8"))
            except UnicodeDecodeError as ue:
                self._send_json_response(400, {
                    "status": "error",
                    "code": 400,
                    "error": "ENCODING_ERROR",
                    "message": f"Ошибка кодировки. Ожидается UTF-8: {ue}"
                })
                return
            except json.JSONDecodeError as je:
                self._send_json_response(400, {
                    "status": "error",
                    "code": 400,
                    "error": "INVALID_JSON_SYNTAX",
                    "message": f"Синтаксическая ошибка в JSON (строка {je.lineno}, колонка {je.colno}): {je.msg}"
                })
                return

            # Проверка типа JSON
            if not isinstance(data, dict):
                self._send_json_response(400, {
                    "status": "error",
                    "code": 400,
                    "error": "INVALID_JSON_STRUCTURE",
                    "message": f"Ожидался JSON объект {...}, получен тип {type(data).__name__}"
                })
                return

            # 2. Проверка команд через JSON body {"command": "stop"} или {"command": "show"}
            if data.get("command") == "stop" or data.get("action") == "stop":
                if self.server.wrapper.on_stop_requested:
                    self.server.wrapper.on_stop_requested()
                print("[REST API] Получена команда остановки записи (через JSON payload)")
                self._send_json_response(200, {
                    "status": "ok",
                    "code": 200,
                    "message": "Команда остановки записи успешно выполнена"
                })
                return

            if data.get("command") == "show" or data.get("action") == "show":
                if self.server.wrapper.on_show_requested:
                    self.server.wrapper.on_show_requested()
                print("[REST API] Получена команда разворачивания окна (через JSON payload)")
                self._send_json_response(200, {
                    "status": "ok",
                    "code": 200,
                    "message": "Окно диктофона развернуто на передний план"
                })
                return

            # 3. Обработка старта сессии (POST /start, /session, /meeting, корень / или если в JSON переданы id и title)
            is_start_path = normalized_path in ("/", "/start", "/api/start", "/session", "/api/session", "/meeting", "/api/meeting")
            has_session_payload = "id" in data and "title" in data

            if is_start_path or has_session_payload:
                # Валидация на пустой объект
                if not data:
                    self._send_json_response(422, {
                        "status": "error",
                        "code": 422,
                        "error": "UNPROCESSABLE_ENTITY",
                        "message": "Передан пустой JSON объект {}. Заполните обязательные поля 'id' и 'title'."
                    })
                    return

                # Валидация обязательных полей
                missing_fields = []
                if "id" not in data or not str(data.get("id", "")).strip():
                    missing_fields.append("id")
                if "title" not in data or not str(data.get("title", "")).strip():
                    missing_fields.append("title")

                if missing_fields:
                    self._send_json_response(400, {
                        "status": "error",
                        "code": 400,
                        "error": "MISSING_REQUIRED_FIELDS",
                        "missing_fields": missing_fields,
                        "message": f"Отсутствуют обязательные поля в JSON пакете: {', '.join(missing_fields)}"
                    })
                    return

                # Проверка занятости (уже идет запись)
                if self.server.wrapper.is_recording_func():
                    print(f"[REST API] Запрос отклонен: рекордер занят активной записью")
                    self._send_json_response(409, {
                        "status": "error",
                        "code": "BUSY",
                        "status_code": 409,
                        "error": "RECORDER_BUSY",
                        "message": "Сервер занят: в данный момент уже идет сессия записи аудио. Отправьте POST /stop для завершения."
                    })
                    return

                # Нормализация структуры данных сессии
                if "participants" not in data:
                    data["participants"] = []
                elif isinstance(data["participants"], str):
                    data["participants"] = [p.strip() for p in data["participants"].split(",") if p.strip()]

                if "agenda" not in data:
                    data["agenda"] = []

                print(f"[REST API] Принята новая сессия: id='{data.get('id')}', title='{data.get('title')}'")
                
                # Запуск записи в приложении через callback
                if self.server.wrapper.on_session_received:
                    self.server.wrapper.on_session_received(data)

                # Ответ 200 OK при успешном получении JSON
                self._send_json_response(200, {
                    "status": "ok",
                    "code": 200,
                    "message": "Метаданные сессии успешно приняты. Запись звука запущена.",
                    "id": data.get("id"),
                    "title": data.get("title"),
                    "date": data.get("date"),
                    "participants_count": len(data.get("participants", [])),
                    "agenda_count": len(data.get("agenda", []))
                })
                return

            # Неизвестный эндпоинт
            self._send_json_response(404, {
                "status": "error",
                "code": 404,
                "error": "NOT_FOUND",
                "message": f"Неизвестный POST эндпоинт: {self.path}. Используйте POST /start для запуска или POST /stop для остановки."
            })

        except Exception as e:
            print(f"[REST API] Внутренняя ошибка сервера: {e}")
            self._send_json_response(500, {
                "status": "error",
                "code": 500,
                "error": "INTERNAL_SERVER_ERROR",
                "message": f"Внутренняя ошибка сервера: {e}"
            })

    def do_PUT(self):
        self._send_method_not_allowed()

    def do_DELETE(self):
        self._send_method_not_allowed()

    def do_PATCH(self):
        self._send_method_not_allowed()

    def _send_method_not_allowed(self):
        self._send_json_response(405, {
            "status": "error",
            "code": 405,
            "error": "METHOD_NOT_ALLOWED",
            "message": f"HTTP метод {self.command} не поддерживается. Разрешены только GET и POST."
        })


class HTTPServerWrapper(ThreadingHTTPServer):
    wrapper: 'RESTServer'

    def __init__(self, server_address, RequestHandlerClass, wrapper: 'RESTServer'):
        self.wrapper = wrapper
        super().__init__(server_address, RequestHandlerClass)


class RESTServer:
    """
    Легковесный многопоточный HTTP REST сервер для управления диктофоном.
    Заменяет WebSocket:
    - POST /start  : передать JSON метаданных совещания и начать запись
    - POST /stop   : остановить текущую запись
    - GET  /status : проверить статус записи
    """
    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 8765,
        on_session_received: Optional[Callable[[Dict[str, Any]], None]] = None,
        on_stop_requested: Optional[Callable[[], None]] = None,
        on_show_requested: Optional[Callable[[], None]] = None,
        is_recording_func: Optional[Callable[[], bool]] = None
    ):
        self.host = host
        self.port = port
        self.on_session_received = on_session_received
        self.on_stop_requested = on_stop_requested
        self.on_show_requested = on_show_requested
        self.is_recording_func = is_recording_func or (lambda: False)

        self._server: Optional[HTTPServerWrapper] = None
        self._thread: Optional[threading.Thread] = None
        self.is_running = False

    def start(self) -> bool:
        if self.is_running:
            return True

        ready_event = threading.Event()
        start_error = []

        def run_server():
            try:
                server_address = (self.host, self.port)
                self._server = HTTPServerWrapper(server_address, DictaphoneHTTPHandler, self)
                self.is_running = True
                ready_event.set()
                print(f"[REST Server] HTTP сервер успешно запущен на http://{self.host}:{self.port}")
                self._server.serve_forever()
            except Exception as e:
                start_error.append(str(e))
                self.is_running = False
                ready_event.set()

        self._thread = threading.Thread(target=run_server, daemon=True)
        self._thread.start()
        ready_event.wait(timeout=3.0)

        if start_error:
            print(f"[REST Server] Ошибка запуска HTTP сервера: {start_error[0]}")
            return False

        return self.is_running

    def stop(self) -> None:
        if not self.is_running:
            return
        self.is_running = False
        if self._server:
            try:
                self._server.shutdown()
                self._server.server_close()
            except Exception as e:
                print(f"[REST Server] Ошибка остановки сервера: {e}")
        print("[REST Server] HTTP сервер остановлен")
