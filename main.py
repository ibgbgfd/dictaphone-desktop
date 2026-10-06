import sys
import os
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Add current workspace directory to sys.path
BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt
from ui.main_window import MainWindow
from single_instance import SingleInstanceManager

def main():
    # High DPI scaling
    if hasattr(Qt.ApplicationAttribute, 'AA_EnableHighDpiScaling'):
        QApplication.setAttribute(Qt.ApplicationAttribute.AA_EnableHighDpiScaling, True)
    if hasattr(Qt.ApplicationAttribute, 'AA_UseHighDpiPixmaps'):
        QApplication.setAttribute(Qt.ApplicationAttribute.AA_UseHighDpiPixmaps, True)

    app = QApplication(sys.argv)
    app.setApplicationName("Диктофон")
    app.setOrganizationName("AudioRecorder")
    app.setQuitOnLastWindowClosed(False)  # Keep running in system tray!

    # Проверка одиночного экземпляра приложения
    single_instance = SingleInstanceManager()
    if single_instance.is_already_running():
        # Если приложение уже запущено в фоне/трее, ему отправлен сигнал развернуть окно
        print("[Dictaphone] Приложение уже работает. Окно развернуто на передний план.")
        sys.exit(0)

    # Запускаем слушатель команд для разворачивания окна при будущих повторных запусках
    single_instance.start_listener()

    window = MainWindow()
    single_instance.message_received.connect(lambda msg: window.bring_to_front() if msg == "show" else None)

    if not window.config.start_minimized:
        window.bring_to_front()
    else:
        window.tray_icon.showMessage(
            "Диктофон",
            "Приложение запущено в фоновом режиме (в трее).",
            window.tray_icon.MessageIcon.Information,
            2000
        )

    exit_code = app.exec()
    single_instance.close()
    sys.exit(exit_code)

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        try:
            with open("crash.log", "w", encoding="utf-8") as f:
                f.write(traceback.format_exc())
        except Exception:
            pass
        raise
