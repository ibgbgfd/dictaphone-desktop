import sys
import ctypes
from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtNetwork import QLocalServer, QLocalSocket

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

DEFAULT_SERVER_KEY = "Dictaphone_Single_Instance_Key_2026"
ERROR_ALREADY_EXISTS = 183


class SingleInstanceManager(QObject):
    """
    Менеджер единого экземпляра приложения (Single Instance):
    1. На уровне ОС (Windows Mutex / Local Named Pipe) определяет, запущена ли уже программа.
    2. Если уже запущена — будит существующий процесс ('show'), разворачивает его окно из трея/фона и выходит.
    3. Если не запущена — держит мьютекс и слушает команды повторных запусков.
    """
    message_received = pyqtSignal(str)

    def __init__(self, key: str = DEFAULT_SERVER_KEY, parent=None):
        super().__init__(parent)
        self.key = key
        self.server: QLocalServer = None
        self._mutex_handle = None

    def is_already_running(self) -> bool:
        """
        Проверяет наличие уже запущенного экземпляра.
        Возвращает True, если экземпляр найден.
        """
        # 1. Проверка через Windows Mutex
        if sys.platform == "win32":
            try:
                kernel32 = ctypes.windll.kernel32
                mutex_name = f"Local\\{self.key}"
                self._mutex_handle = kernel32.CreateMutexW(None, False, mutex_name)
                last_error = kernel32.GetLastError()
                if last_error == ERROR_ALREADY_EXISTS:
                    print("[SingleInstance] Обнаружен работающий процесс через Mutex. Отправка сигнала раскрытия окна...")
                    self._notify_running_instance()
                    return True
            except Exception as e:
                print(f"[SingleInstance] Предупреждение Mutex: {e}")

        # 2. Проверка через QLocalSocket (межпроцессный локальный сокет)
        socket = QLocalSocket()
        socket.connectToServer(self.key)
        if socket.waitForConnected(500):
            print("[SingleInstance] Обнаружен работающий процесс через QLocalSocket. Отправка 'show'...")
            socket.write(b"show\n")
            socket.flush()
            socket.waitForBytesWritten(1000)
            socket.waitForReadyRead(100)
            socket.disconnectFromServer()
            return True

        return False

    def _notify_running_instance(self):
        """Оповещает уже работающий экземпляр через сокет, HTTP эндпоинт и WinAPI."""
        # Канал 1: QLocalSocket
        try:
            s = QLocalSocket()
            s.connectToServer(self.key)
            if s.waitForConnected(600):
                s.write(b"show\n")
                s.flush()
                s.waitForBytesWritten(1000)
                s.waitForReadyRead(100)
                s.disconnectFromServer()
                return
        except Exception:
            pass

        # Канал 2: Локальный HTTP REST эндпоинт /show
        try:
            import requests
            requests.post("http://127.0.0.1:8765/show", timeout=0.8)
            return
        except Exception:
            pass

        # Канал 3: Прямой вызов WinAPI ShowWindow
        if sys.platform == "win32":
            try:
                user32 = ctypes.windll.user32
                hwnd = user32.FindWindowW(None, "Аудиорекордер — Диктофон совещаний")
                if hwnd:
                    user32.ShowWindow(hwnd, 9)  # SW_RESTORE
                    user32.SetForegroundWindow(hwnd)
            except Exception:
                pass

    def start_listener(self) -> bool:
        """
        Запускает слушающий сокет-сервер в основном процессе.
        """
        QLocalServer.removeServer(self.key)
        self.server = QLocalServer(self)
        self.server.newConnection.connect(self._on_new_connection)
        
        success = self.server.listen(self.key)
        if success:
            print(f"[SingleInstance] Служба единого экземпляра активна (ключ: {self.key})")
        return success

    def _on_new_connection(self):
        client_socket = self.server.nextPendingConnection()
        if not client_socket:
            return

        def handle_data():
            data = client_socket.readAll().data().decode("utf-8", errors="ignore").strip()
            if data:
                print(f"[SingleInstance] Получена команда от повторного запуска: '{data}'")
                self.message_received.emit(data)
            client_socket.disconnectFromServer()

        if client_socket.bytesAvailable() > 0:
            handle_data()
        else:
            client_socket.readyRead.connect(handle_data)

    def close(self):
        if self.server:
            self.server.close()
            QLocalServer.removeServer(self.key)
        if sys.platform == "win32" and self._mutex_handle:
            try:
                ctypes.windll.kernel32.CloseHandle(self._mutex_handle)
                self._mutex_handle = None
            except Exception:
                pass
