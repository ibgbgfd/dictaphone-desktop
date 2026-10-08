import os
import sys
import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QComboBox, QLineEdit, QTableWidget, QTableWidgetItem,
    QHeaderView, QSystemTrayIcon, QMenu, QMessageBox, QGroupBox,
    QAbstractItemView, QStatusBar, QFileDialog, QProgressBar,
    QTabWidget, QListWidget, QSplitter
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QObject
from PyQt6.QtGui import QIcon, QAction, QColor

from config import AppConfig, load_config, save_config
from database import Database, STATUS_SENT, STATUS_NOT_SENT, STATUS_IN_PROGRESS
from audio_recorder import AudioRecorder
from ui.settings_dialog import SettingsDialog
from http_server import RESTServer
from uploader import UploadWorker
from session_validator import validate_session_data, EXAMPLE_SESSION_PAYLOAD


class RecorderBridge(QObject):
    """Потокобезопасный мост сигналов между аудио-потоками, REST-сервером и UI."""
    level_updated = pyqtSignal(float)
    error_occurred = pyqtSignal(str)
    session_received = pyqtSignal(dict)
    stop_requested = pyqtSignal()
    show_requested = pyqtSignal()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Аудиорекордер — Диктофон совещаний")
        self.resize(920, 680)

        # Конфигурация и база данных
        self.config: AppConfig = load_config()
        self.db = Database()

        # Данные активной сессии
        self.current_meeting_data: Dict[str, Any] = {}
        self.pending_session_id: Optional[str] = None
        self.active_workers: List[UploadWorker] = []

        # Сигнальный мост
        self.bridge = RecorderBridge()
        self.bridge.level_updated.connect(self._on_audio_level)
        self.bridge.error_occurred.connect(self._on_audio_error)
        self.bridge.session_received.connect(self._on_session_received)
        self.bridge.stop_requested.connect(self._on_stop_requested)
        self.bridge.show_requested.connect(self.bring_to_front)

        # Аудио рекордер
        self.recorder: Optional[AudioRecorder] = None
        self.current_recording_id: Optional[int] = None
        self.current_filepath: Optional[str] = None

        # Таймер длительности записи
        self.record_timer = QTimer(self)
        self.record_timer.setInterval(200)
        self.record_timer.timeout.connect(self._update_record_time)

        # Ротация старых файлов при запуске (триггер №1 из ТЗ)
        self._purge_old_files()

        # UI
        icon_path = Path(__file__).resolve().parent.parent / "resources" / "icon.png"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

        self._init_ui()
        self._setup_tray()
        self._refresh_history()

        # Запуск HTTP REST сервера для приема сессий и команд
        self.server = RESTServer(
            host=self.config.http_host,
            port=self.config.http_port,
            on_session_received=lambda data: self.bridge.session_received.emit(data),
            on_stop_requested=lambda: self.bridge.stop_requested.emit(),
            on_show_requested=lambda: self.bridge.show_requested.emit(),
            is_recording_func=self._is_recording_active
        )
        srv_ok = self.server.start()
        if srv_ok:
            self._set_status_text("Ожидание REST запросов")
        else:
            self._set_status_text(f"Ошибка запуска HTTP сервера на порту {self.config.http_port}")

    def _is_recording_active(self) -> bool:
        return bool(self.recorder and self.recorder.is_recording)

    def _purge_old_files(self) -> None:
        """Очистка старых файлов старше max_storage_days по БД и на диске."""
        try:
            self.db.purge_old_recordings(self.config.max_storage_days)
        except Exception as e:
            print(f"[Purge] Ошибка очистки старых файлов: {e}")

    def _set_status_text(self, text: str) -> None:
        """Централизованное обновление статус-строки (ТЗ п. 3)."""
        host = getattr(self.config, 'http_host', '0.0.0.0')
        port = getattr(self.config, 'http_port', 8765)
        self.status_bar.showMessage(f"{text}  |  HTTP: http://{host}:{port}")

    def _init_ui(self) -> None:
        central_widget = QWidget(self)
        self.setCentralWidget(central_widget)

        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(8)

        # Панель управления верхняя (Тестовая загрузка + Настройки)
        top_bar = QHBoxLayout()
        top_bar.setSpacing(8)

        self.load_json_btn = QPushButton("Загрузить JSON...")
        self.load_json_btn.setToolTip("Тестовая загрузка пакета совещания из файла JSON")
        self.load_json_btn.clicked.connect(self._load_json_file)

        self.settings_btn = QPushButton("Настройки")
        self.settings_btn.clicked.connect(self._open_settings)

        top_bar.addStretch()
        top_bar.addWidget(self.load_json_btn)
        top_bar.addWidget(self.settings_btn)

        main_layout.addLayout(top_bar)

        # Табы: 1) Текущая запись / Совещание, 2) История записей
        self.tab_widget = QTabWidget()
        main_layout.addWidget(self.tab_widget, 1)

        # ===============================================================
        # Вкладка 1: Главный экран текущей сессии
        # ===============================================================
        main_tab = QWidget()
        tab1_layout = QVBoxLayout(main_tab)
        tab1_layout.setContentsMargins(8, 8, 8, 8)
        tab1_layout.setSpacing(8)

        # Блок общей информации о текущей записи (title и date из ТЗ)
        meta_group = QGroupBox("Информация о текущей сессии")
        meta_layout = QVBoxLayout(meta_group)
        meta_layout.setSpacing(6)

        # Строка Название / Тема (title)
        title_row = QHBoxLayout()
        title_lbl = QLabel("Тема:")
        title_lbl.setFixedWidth(80)
        title_row.addWidget(title_lbl)
        self.title_input = QLineEdit()
        self.title_input.setPlaceholderText("Ожидание данных совещания из REST API...")
        title_row.addWidget(self.title_input, 1)
        meta_layout.addLayout(title_row)

        # Строка Дата и ID
        date_id_row = QHBoxLayout()
        date_lbl = QLabel("Дата и время:")
        date_lbl.setFixedWidth(80)
        date_id_row.addWidget(date_lbl)
        self.date_input = QLineEdit()
        self.date_input.setPlaceholderText("—")
        date_id_row.addWidget(self.date_input, 1)

        id_lbl = QLabel("ID сессии:")
        id_lbl.setFixedWidth(60)
        date_id_row.addWidget(id_lbl)
        self.id_input = QLineEdit()
        self.id_input.setPlaceholderText("—")
        self.id_input.setFixedWidth(200)
        date_id_row.addWidget(self.id_input)

        meta_layout.addLayout(date_id_row)
        tab1_layout.addWidget(meta_group)

        # Сплиттер для Участников и Повестки дня (ТЗ п. 3)
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # 1. Блок «Участники» (построчный вывод массива participants)
        part_group = QGroupBox("Участники")
        part_layout = QVBoxLayout(part_group)
        part_layout.setContentsMargins(6, 6, 6, 6)
        self.participants_list = QListWidget()
        self.participants_list.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        part_layout.addWidget(self.participants_list)
        splitter.addWidget(part_group)

        # 2. Блок «Повестка дня» (таблица: вопрос, исполнитель, должность, отдел)
        agenda_group = QGroupBox("Повестка дня")
        agenda_layout = QVBoxLayout(agenda_group)
        agenda_layout.setContentsMargins(6, 6, 6, 6)
        self.agenda_table = QTableWidget()
        self.agenda_table.setColumnCount(4)
        self.agenda_table.setHorizontalHeaderLabels([
            "Вопрос", "Исполнитель", "Должность", "Подразделение"
        ])
        self.agenda_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.agenda_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.agenda_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.agenda_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.agenda_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.agenda_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        agenda_layout.addWidget(self.agenda_table)
        splitter.addWidget(agenda_group)

        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        tab1_layout.addWidget(splitter, 1)

        # Блок индикаторов и управления записью
        ctrl_group = QGroupBox("Управление записью")
        ctrl_layout = QVBoxLayout(ctrl_group)
        ctrl_layout.setContentsMargins(12, 10, 12, 10)
        ctrl_layout.setSpacing(6)

        # Графический индикатор уровня звука (Windows 11 Fluent VU-метр)
        level_row = QHBoxLayout()
        level_row.setSpacing(10)
        level_lbl = QLabel("Уровень звука:")
        level_lbl.setFixedWidth(105)
        level_row.addWidget(level_lbl)

        self.level_bar = QProgressBar()
        self.level_bar.setRange(0, 100)
        self.level_bar.setValue(0)
        self.level_bar.setTextVisible(False)
        self.level_bar.setFixedHeight(8)
        self.level_bar.setToolTip("Индикатор входящего сигнала громкости с микрофона")
        self.level_bar.setStyleSheet("""
            QProgressBar {
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 4px;
                background-color: #202020;
            }
            QProgressBar::chunk {
                background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0.0 #107c41, stop:0.75 #107c41, stop:0.90 #ffb900, stop:1.0 #e81123);
                border-radius: 3px;
            }
        """)
        level_row.addWidget(self.level_bar, 1)
        ctrl_layout.addLayout(level_row)

        # Таймер и кнопки управления записью (Windows 11 Fluent Design)
        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(10)

        timer_title = QLabel("Длительность:")
        timer_title.setFixedWidth(105)
        bottom_row.addWidget(timer_title)

        self.timer_label = QLabel("00:00:00")
        self.timer_label.setMinimumHeight(34)
        self.timer_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        bottom_row.addWidget(self.timer_label)

        bottom_row.addStretch()

        # Кнопка паузы (Windows 11 Secondary Button)
        self.pause_btn = QPushButton("⏸   Пауза")
        self.pause_btn.setMinimumHeight(34)
        self.pause_btn.setMinimumWidth(110)
        self.pause_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.pause_btn.clicked.connect(self._toggle_pause)
        self.pause_btn.setVisible(False)
        bottom_row.addWidget(self.pause_btn)

        # Кнопка старта / остановки записи (Windows 11 Fluent Button)
        self.record_toggle_btn = QPushButton("▶   Запустить запись")
        self.record_toggle_btn.setMinimumHeight(34)
        self.record_toggle_btn.setMinimumWidth(190)
        self.record_toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.record_toggle_btn.clicked.connect(self._toggle_recording)
        self._update_record_button_ui(is_recording=False)
        bottom_row.addWidget(self.record_toggle_btn)

        ctrl_layout.addLayout(bottom_row)
        tab1_layout.addWidget(ctrl_group)

        self.tab_widget.addTab(main_tab, "Текущая запись")

        # ===============================================================
        # Вкладка 2: Экран истории (ТЗ п. 3)
        # ===============================================================
        history_tab = QWidget()
        hist_layout = QVBoxLayout(history_tab)
        hist_layout.setContentsMargins(8, 8, 8, 8)
        hist_layout.setSpacing(6)

        hist_top = QHBoxLayout()
        hist_top.addWidget(QLabel("<b>Архив локальных записей и статус отправки на сервер:</b>"))
        hist_top.addStretch()

        self.open_all_folder_btn = QPushButton("Открыть папку записей")
        self.open_all_folder_btn.clicked.connect(self._open_recordings_folder)
        hist_top.addWidget(self.open_all_folder_btn)

        self.refresh_hist_btn = QPushButton("Обновить таблицу")
        self.refresh_hist_btn.clicked.connect(self._refresh_history)
        hist_top.addWidget(self.refresh_hist_btn)

        hist_layout.addLayout(hist_top)

        # Таблица истории по ТЗ: Дата, Тема, Статус отправки, Действия
        self.history_table = QTableWidget()
        self.history_table.setColumnCount(6)
        self.history_table.setHorizontalHeaderLabels([
            "Дата", "Тема", "Длительность", "Размер", "Статус отправки", "Действия"
        ])
        self.history_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.history_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.history_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.history_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.history_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.history_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
        self.history_table.setColumnWidth(5, 300)
        self.history_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.history_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        hist_layout.addWidget(self.history_table, 1)

        self.tab_widget.addTab(history_tab, "История записей")

        # Статус-бар
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self._set_status_text("Ожидание подключения")

    def _setup_tray(self) -> None:
        """Системный трей по ТЗ п. 3: сворачивание крестиком, разворачивание по двойному клику."""
        self.tray_icon = QSystemTrayIcon(self)
        icon_path = Path(__file__).resolve().parent.parent / "resources" / "icon.png"
        if icon_path.exists():
            self.tray_icon.setIcon(QIcon(str(icon_path)))

        tray_menu = QMenu()
        show_action = QAction("Показать окно", self)
        show_action.triggered.connect(self._show_window)

        self.tray_stop_action = QAction("Остановить запись", self)
        self.tray_stop_action.triggered.connect(self._stop_recording)
        self.tray_stop_action.setEnabled(False)

        settings_action = QAction("Настройки...", self)
        settings_action.triggered.connect(self._open_settings)

        quit_action = QAction("Выход", self)
        quit_action.triggered.connect(self._force_quit)

        tray_menu.addAction(show_action)
        tray_menu.addAction(self.tray_stop_action)
        tray_menu.addAction(settings_action)
        tray_menu.addSeparator()
        tray_menu.addAction(quit_action)

        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.activated.connect(self._on_tray_activated)
        self.tray_icon.show()

    def _on_tray_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._show_window()

    def _show_window(self) -> None:
        self.bring_to_front()

    def bring_to_front(self) -> None:
        """Разворачивает окно из трея/фона и выдвигает на передний план поверх всех окон."""
        if not self.isVisible():
            self.show()
        if self.isMinimized():
            self.showNormal()

        self.setWindowState((self.windowState() & ~Qt.WindowState.WindowMinimized) | Qt.WindowState.WindowActive)
        self.showNormal()
        self.activateWindow()
        self.raise_()

        # Для Windows: гарантированное принудительное выдвижение окна на передний план
        if sys.platform == "win32":
            try:
                import ctypes
                hwnd = int(self.winId())
                ctypes.windll.user32.ShowWindow(hwnd, 9)  # SW_RESTORE = 9
                ctypes.windll.user32.SetForegroundWindow(hwnd)
            except Exception:
                pass

    def _force_quit(self) -> None:
        if self.recorder and self.recorder.is_recording:
            reply = QMessageBox.question(
                self,
                "Идет запись",
                "Сейчас идет запись. Остановить и закрыть программу?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
            self._stop_recording()

        if hasattr(self, 'server') and self.server:
            self.server.stop()

        self.tray_icon.hide()
        self.close()
        sys.exit(0)

    def closeEvent(self, event) -> None:
        if self.config.minimize_to_tray:
            event.ignore()
            self.hide()
            self.tray_icon.showMessage(
                "Диктофон",
                "Программа свернута в трей и продолжает работать.",
                QSystemTrayIcon.MessageIcon.Information,
                1500
            )
        else:
            if self.recorder and self.recorder.is_recording:
                self._stop_recording()
            if hasattr(self, 'ws_server') and self.ws_server:
                self.ws_server.stop()
            event.accept()

    def _open_settings(self) -> None:
        if self.recorder and self.recorder.is_recording:
            QMessageBox.information(self, "Запись активна", "Пожалуйста, остановите запись перед изменением настроек.")
            return

        old_host = self.config.http_host
        old_port = self.config.http_port
        dialog = SettingsDialog(self.config, self)
        if dialog.exec():
            Path(self.config.recordings_dir).mkdir(parents=True, exist_ok=True)
            self._purge_old_files()
            if self.config.http_host != old_host or self.config.http_port != old_port:
                self._restart_server()

    def _restart_server(self) -> None:
        if hasattr(self, 'server') and self.server:
            self.server.stop()
        self.server = RESTServer(
            host=self.config.http_host,
            port=self.config.http_port,
            on_session_received=lambda data: self.bridge.session_received.emit(data),
            on_stop_requested=lambda: self.bridge.stop_requested.emit(),
            on_show_requested=lambda: self.bridge.show_requested.emit(),
            is_recording_func=self._is_recording_active
        )
        self.server.start()
        self._set_status_text("Ожидание REST запросов")

    # ===============================================================
    # Обработка входящего REST JSON (Старт сессии и запись)
    # ===============================================================
    def _on_session_received(self, data: dict) -> None:
        """
        Триггер №2 ротации и автоматический старт записи звука.
        """
        # 1. Ротация старых файлов строго перед стартом новой записи (ТЗ п. 2.3)
        self._purge_old_files()

        # 2. Парсинг и заполнение UI
        title = str(data.get("title", "")).strip()
        date_str = str(data.get("date", "")).strip()
        session_id = str(data.get("id", "")).strip()
        participants = data.get("participants", [])
        agenda = data.get("agenda", [])

        self.current_meeting_data = data
        self.pending_session_id = session_id or None

        self.title_input.setText(title)
        self.date_input.setText(date_str)
        self.id_input.setText(session_id)

        # Заполнение списка участников (построчно)
        self.participants_list.clear()
        if isinstance(participants, list):
            for p in participants:
                self.participants_list.addItem(str(p).strip())
        elif isinstance(participants, str):
            for p in participants.split(","):
                if p.strip():
                    self.participants_list.addItem(p.strip())

        # Заполнение таблицы повестки дня
        self.agenda_table.setRowCount(0)
        if isinstance(agenda, list):
            self.agenda_table.setRowCount(len(agenda))
            for row, item in enumerate(agenda):
                if isinstance(item, dict):
                    q = str(item.get("question", ""))
                    a = str(item.get("assignee", ""))
                    pos = str(item.get("position", ""))
                    dep = str(item.get("department", ""))
                else:
                    q = str(item)
                    a, pos, dep = "", "", ""

                self.agenda_table.setItem(row, 0, QTableWidgetItem(q))
                self.agenda_table.setItem(row, 1, QTableWidgetItem(a))
                self.agenda_table.setItem(row, 2, QTableWidgetItem(pos))
                self.agenda_table.setItem(row, 3, QTableWidgetItem(dep))

        # Переключение на главный экран
        self.tab_widget.setCurrentIndex(0)
        self._show_window()

        self.tray_icon.showMessage(
            "Входящая сессия (REST API)",
            f"{title}\nЗапись звука запущена автоматически.",
            QSystemTrayIcon.MessageIcon.Information,
            4000
        )

        # 3. Триггер старта: Запись звука стартует автоматически сразу после получения JSON (ТЗ п. 2.1)
        self._start_recording()

    def _on_stop_requested(self) -> None:
        """Команда остановки по WS (ТЗ п. 2.1)."""
        if self.recorder and self.recorder.is_recording:
            self._stop_recording()

    def _load_json_file(self) -> None:
        """Тестовая загрузка JSON из файла с проверками и ответами на ошибки пользователя."""
        if self._is_recording_active():
            QMessageBox.warning(
                self,
                "Запись активна",
                "Невозможно загрузить новую сессию во время активной записи аудио.\n\n"
                "Сначала остановите текущую запись кнопкой «Остановить запись»."
            )
            return

        base_dir = Path(__file__).resolve().parent.parent
        default_file = base_dir / "data.json"
        start_path = str(default_file if default_file.exists() else base_dir)

        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Выберите файл сессии (JSON)",
            start_path,
            "JSON файлы (*.json);;Все файлы (*.*)"
        )
        if not file_path:
            return

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
        except UnicodeDecodeError:
            try:
                with open(file_path, "r", encoding="cp1251") as f:
                    content = f.read()
            except Exception as e:
                QMessageBox.critical(self, "Ошибка кодировки", f"Не удалось прочитать файл. Ожидается кодировка UTF-8:\n{e}")
                return
        except Exception as e:
            QMessageBox.critical(self, "Ошибка чтения", f"Не удалось прочитать выбранный файл:\n{e}")
            return

        if not content.strip():
            QMessageBox.warning(
                self,
                "Пустой файл",
                "Выбранный JSON файл пуст.\n\n"
                "Укажите корректный файл совещания с обязательными полями 'id' и 'title'."
            )
            return

        try:
            raw_data = json.loads(content)
        except json.JSONDecodeError as je:
            QMessageBox.critical(
                self,
                "Синтаксическая ошибка JSON",
                f"Ошибка в синтаксисе JSON файла:\n\n"
                f"Строка: {je.lineno}, Позиция: {je.colno}\n"
                f"Сообщение: {je.msg}\n\n"
                f"Проверьте правильность скобок, кавычек и запятых в JSON."
            )
            return

        is_valid, err_msg, normalized_data, missing_fields = validate_session_data(raw_data)
        if not is_valid:
            example_str = json.dumps(EXAMPLE_SESSION_PAYLOAD, ensure_ascii=False, indent=2)
            QMessageBox.critical(
                self,
                "Ошибка валидации сессии",
                f"{err_msg}\n\n"
                f"Пример корректного формата JSON:\n{example_str}"
            )
            return

        # Передаем валидированные данные в UI
        self._on_session_received(normalized_data)

    # ===============================================================
    # Управление записью звука (WebM Opus)
    # ===============================================================
    def _manual_start_recording(self) -> None:
        self._purge_old_files()
        
        now = datetime.now()
        title = self.title_input.text().strip()
        if not title:
            title = f"Совещание от {now.strftime('%d.%m.%Y %H:%M')}"
            self.title_input.setText(title)

        date_val = self.date_input.text().strip()
        if not date_val:
            date_val = now.strftime("%d/%m/%Y %H:%M")
            self.date_input.setText(date_val)

        id_val = self.id_input.text().strip()
        if not id_val:
            id_val = f"совещание_{now.strftime('%Y%m%d_%H%M%S')}"
            self.id_input.setText(id_val)

        self.pending_session_id = id_val
        self.current_meeting_data = {
            "id": id_val,
            "title": title,
            "date": date_val,
            "participants": [self.participants_list.item(i).text() for i in range(self.participants_list.count())],
            "agenda": []
        }
        self._start_recording()

    def _start_recording(self) -> None:
        if self.recorder and self.recorder.is_recording:
            return

        dev_index = self.config.device_index if self.config.device_index >= 0 else None
        title = self.title_input.text().strip() or f"Запись {datetime.now().strftime('%d.%m.%Y %H:%M')}"
        now_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"rec_{now_str}.webm"
        recordings_dir = Path(self.config.recordings_dir)
        recordings_dir.mkdir(parents=True, exist_ok=True)
        filepath = str(recordings_dir / filename)

        self.recorder = AudioRecorder(
            sample_rate=self.config.sample_rate,
            channels=self.config.channels,
            codec_name=self.config.audio_codec,
            bitrate=self.config.bitrate,
            device_index=dev_index,
            on_level_callback=lambda lvl: self.bridge.level_updated.emit(lvl),
            on_error_callback=lambda err: self.bridge.error_occurred.emit(err)
        )

        ok = self.recorder.start_recording(filepath)
        if not ok:
            self._set_status_text("Ошибка аудиоустройства")
            self._update_record_button_ui(is_recording=False)
            self.load_json_btn.setEnabled(True)
            self.settings_btn.setEnabled(True)
            self.level_bar.setValue(0)
            QMessageBox.critical(
                self,
                "Ошибка микрофона",
                "Не удалось запустить запись звука с микрофона.\n\n"
                "Возможные причины:\n"
                "• Микрофон не подключен или занят другим процессом\n"
                "• Выбрано некорректное устройство в Настройках\n\n"
                "Проверьте настройки звука в окне «Настройки» -> «Аудио»."
            )
            return

        session_id = self.pending_session_id or f"rec_{now_str}"
        meeting_meta = dict(self.current_meeting_data) if self.current_meeting_data else {
            "id": session_id,
            "title": title,
            "date": self.date_input.text().strip()
        }

        # Список участников для БД
        parts_list = []
        for i in range(self.participants_list.count()):
            parts_list.append(self.participants_list.item(i).text())
        parts_str = ", ".join(parts_list)

        self.current_filepath = filepath
        self.current_recording_id = self.db.add_recording(
            filepath=filepath,
            title=title,
            participants=parts_str,
            session_id=session_id,
            status=STATUS_IN_PROGRESS,
            metadata=meeting_meta
        )

        # UI состояние
        self._update_record_button_ui(is_recording=True)
        self.tray_stop_action.setEnabled(True)
        self.load_json_btn.setEnabled(False)
        self.settings_btn.setEnabled(False)

        self._set_status_text("Идет запись...")
        self.record_timer.start()

    def _stop_recording(self) -> None:
        if not self.recorder or not self.recorder.is_recording:
            return

        self._set_status_text("Сохранение файла...")
        self.record_timer.stop()
        duration = self.recorder.stop_recording()

        file_size = 0
        if self.current_filepath and Path(self.current_filepath).exists():
            file_size = Path(self.current_filepath).stat().st_size

        # Формирование и сохранение исходного JSON-файла рядом с .webm
        json_filepath = str(Path(self.current_filepath).with_suffix(".json"))
        full_json = dict(self.current_meeting_data) if self.current_meeting_data else {}
        session_id = self.pending_session_id or f"rec_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        if not full_json.get("id"):
            full_json["id"] = session_id
        if not full_json.get("title"):
            full_json["title"] = self.title_input.text().strip() or f"Запись {datetime.now().strftime('%d.%m.%Y %H:%M')}"
        if not full_json.get("date"):
            full_json["date"] = self.date_input.text().strip() or datetime.now().strftime("%d/%m/%Y %H:%M")

        parts_list = []
        for i in range(self.participants_list.count()):
            parts_list.append(self.participants_list.item(i).text())
        if not full_json.get("participants"):
            full_json["participants"] = parts_list

        full_json["recording"] = {
            "filename": Path(self.current_filepath).name,
            "duration": round(duration, 2),
            "file_size": file_size,
            "recorded_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }

        try:
            with open(json_filepath, "w", encoding="utf-8") as f:
                json.dump(full_json, f, ensure_ascii=False, indent=2)
            print(f"[Record] Сохранен JSON-пакет сессии: {json_filepath}")
        except Exception as e:
            print(f"[Record] Ошибка сохранения JSON файла: {e}")

        # Обновление записи в SQLite БД
        rec_id = self.current_recording_id
        if rec_id:
            self.db.update_recording(
                rec_id,
                duration=duration,
                file_size=file_size,
                status=STATUS_IN_PROGRESS,
                json_filepath=json_filepath,
                metadata=full_json
            )

        # Сброс UI
        self._update_record_button_ui(is_recording=False)
        self.tray_stop_action.setEnabled(False)

        self.load_json_btn.setEnabled(True)
        self.settings_btn.setEnabled(True)

        current_audio = self.current_filepath
        self.pending_session_id = None
        self.current_meeting_data = {}

        self._refresh_history()

        # Автоматическая отправка данных (HTTP POST) по окончании записи (ТЗ п. 2.1)
        if getattr(self.config, 'auto_upload', True) and self.config.upload_url and rec_id and current_audio:
            self._trigger_upload(rec_id, current_audio, json_filepath)
        else:
            if rec_id:
                self.db.update_recording(rec_id, status=STATUS_NOT_SENT)
            self._set_status_text("Ожидание подключения")
            self._refresh_history()

    # ===============================================================
    # Отправка данных на сервер (HTTP POST multipart/form-data)
    # ===============================================================
    def _trigger_upload(self, rec_id: int, audio_path: str, json_path: str) -> None:
        if not audio_path or not Path(audio_path).exists():
            self.db.update_recording(rec_id, status=STATUS_NOT_SENT)
            self._refresh_history()
            self._set_status_text("Ошибка: файл не найден")
            QMessageBox.critical(
                self,
                "Файл не найден",
                f"Аудиофайл не найден на локальном диске:\n{audio_path}\n\n"
                f"Возможно, файл был перемещен или удален."
            )
            return

        if not self.config.upload_url:
            self.db.update_recording(rec_id, status=STATUS_NOT_SENT)
            self._refresh_history()
            self._set_status_text("Ошибка сети")
            QMessageBox.warning(self, "Отправка", "Не задан URL сервера для выгрузки. Настройте его в Настройках.")
            return

        self.db.update_recording(rec_id, status=STATUS_IN_PROGRESS)
        self._refresh_history()
        self._set_status_text("Отправка данных на сервер...")

        worker = UploadWorker(
            rec_id=rec_id,
            audio_path=audio_path,
            json_path=json_path,
            upload_url=self.config.upload_url,
            max_retries=self.config.max_upload_retries,
            verify_ssl=getattr(self.config, 'verify_ssl', False)
        )
        worker.progress.connect(self._on_upload_progress)
        worker.finished.connect(self._on_upload_finished)
        self.active_workers.append(worker)
        worker.start()

    def _on_upload_progress(self, rec_id: int, msg: str) -> None:
        self.db.update_recording(rec_id, status=STATUS_IN_PROGRESS)
        self._refresh_history()
        self._set_status_text(msg)

    def _on_upload_finished(self, rec_id: int, success: bool, msg: str) -> None:
        """
        ТЗ п. 2.1:
        При ошибке отправки программа делает до 5 попыток.
        Если сеть отсутствует -> статус «Не отправлено».
        При успехе -> статус «Отправлено».
        """
        new_status = STATUS_SENT if success else STATUS_NOT_SENT
        self.db.update_recording(rec_id, status=new_status)
        self._refresh_history()

        if success:
            self._set_status_text("Отправлено")
            self.tray_icon.showMessage(
                "Выгрузка завершена",
                "Аудиофайл WebM и JSON успешно отправлены на сервер.",
                QSystemTrayIcon.MessageIcon.Information,
                3000
            )
        else:
            self._set_status_text("Ошибка сети")
            self.tray_icon.showMessage(
                "Ошибка сети",
                f"Не удалось отправить файл (5 попыток). Статус: {STATUS_NOT_SENT}",
                QSystemTrayIcon.MessageIcon.Warning,
                4000
            )

    def _update_record_time(self) -> None:
        if self.recorder and self.recorder.is_recording:
            if getattr(self.recorder, 'is_paused', False):
                return
            paused_total = getattr(self.recorder, 'total_paused_time', 0.0)
            elapsed = int(datetime.now().timestamp() - self.recorder.start_time - paused_total)
            elapsed = max(0, elapsed)
            hours = elapsed // 3600
            mins = (elapsed % 3600) // 60
            secs = elapsed % 60
            self.timer_label.setText(f"●  {hours:02d}:{mins:02d}:{secs:02d}")

    def _toggle_recording(self) -> None:
        """Единое действие для кнопки: старт записи или её остановка."""
        if self.recorder and self.recorder.is_recording:
            self._stop_recording()
        else:
            self._manual_start_recording()

    def _toggle_pause(self) -> None:
        """Переключает паузу записи (Windows 11 Fluent)."""
        if not self.recorder or not self.recorder.is_recording:
            return
        if self.recorder.is_paused:
            self.recorder.resume_recording()
            self._set_status_text("Запись возобновлена")
            self._update_record_button_ui(is_recording=True, is_paused=False)
        else:
            self.recorder.pause_recording()
            self._set_status_text("Запись приостановлена (пауза)")
            self._update_record_button_ui(is_recording=True, is_paused=True)

    def _update_record_button_ui(self, is_recording: bool, is_paused: bool = False) -> None:
        """Переключает внешний вид кнопок управления, таймера и шкалы в стиле Windows 11 Fluent UI."""
        if hasattr(self, 'pause_btn'):
            self.pause_btn.setVisible(is_recording)

        if is_recording:
            if is_paused:
                if hasattr(self, 'pause_btn'):
                    self.pause_btn.setText("▶   Возобновить")
                    self.pause_btn.setStyleSheet("""
                        QPushButton {
                            background-color: #38311e;
                            color: #fde047;
                            font-family: 'Segoe UI Variable Text', 'Segoe UI', -apple-system, sans-serif;
                            font-size: 13px;
                            font-weight: 600;
                            border-radius: 6px;
                            padding: 6px 16px;
                            border: 1px solid rgba(253, 224, 71, 0.35);
                        }
                        QPushButton:hover {
                            background-color: #4a4128;
                            border-color: rgba(253, 224, 71, 0.55);
                            color: #ffffff;
                        }
                        QPushButton:pressed {
                            background-color: #292416;
                        }
                    """)
                self.timer_label.setStyleSheet("""
                    QLabel {
                        background-color: #2b2518;
                        color: #fde047;
                        font-family: 'Segoe UI Variable Display', 'Segoe UI', 'Cascadia Mono', monospace;
                        font-size: 15px;
                        font-weight: 600;
                        padding: 4px 16px;
                        border-radius: 6px;
                        border: 1px solid rgba(253, 224, 71, 0.35);
                    }
                """)
                if hasattr(self, 'level_bar'):
                    self.level_bar.setValue(0)
            else:
                if hasattr(self, 'pause_btn'):
                    self.pause_btn.setText("⏸   Пауза")
                    self.pause_btn.setStyleSheet("""
                        QPushButton {
                            background-color: #2d2d2d;
                            color: #f3f3f3;
                            font-family: 'Segoe UI Variable Text', 'Segoe UI', -apple-system, sans-serif;
                            font-size: 13px;
                            font-weight: 600;
                            border-radius: 6px;
                            padding: 6px 16px;
                            border: 1px solid rgba(255, 255, 255, 0.09);
                            border-top: 1px solid rgba(255, 255, 255, 0.15);
                        }
                        QPushButton:hover {
                            background-color: #383838;
                            border-color: rgba(255, 255, 255, 0.22);
                            color: #ffffff;
                        }
                        QPushButton:pressed {
                            background-color: #242424;
                            border-color: rgba(255, 255, 255, 0.06);
                        }
                    """)
                self.timer_label.setStyleSheet("""
                    QLabel {
                        background-color: #2c1d20;
                        color: #ff99a4;
                        font-family: 'Segoe UI Variable Display', 'Segoe UI', 'Cascadia Mono', monospace;
                        font-size: 15px;
                        font-weight: 600;
                        padding: 4px 16px;
                        border-radius: 6px;
                        border: 1px solid rgba(232, 17, 35, 0.40);
                    }
                """)

            self.record_toggle_btn.setText("⏹   Остановить запись")
            self.record_toggle_btn.setStyleSheet("""
                QPushButton {
                    background-color: #c42b1c;
                    color: #ffffff;
                    font-family: 'Segoe UI Variable Text', 'Segoe UI', -apple-system, sans-serif;
                    font-size: 13px;
                    font-weight: 600;
                    border-radius: 6px;
                    padding: 6px 18px;
                    border: 1px solid rgba(255, 255, 255, 0.12);
                    border-top: 1px solid rgba(255, 255, 255, 0.25);
                }
                QPushButton:hover {
                    background-color: #d83b2a;
                    border-color: rgba(255, 255, 255, 0.30);
                }
                QPushButton:pressed {
                    background-color: #a82315;
                    border-color: rgba(255, 255, 255, 0.08);
                }
            """)
        else:
            self.record_toggle_btn.setText("▶   Запустить запись")
            self.record_toggle_btn.setStyleSheet("""
                QPushButton {
                    background-color: #0078d4;
                    color: #ffffff;
                    font-family: 'Segoe UI Variable Text', 'Segoe UI', -apple-system, sans-serif;
                    font-size: 13px;
                    font-weight: 600;
                    border-radius: 6px;
                    padding: 6px 18px;
                    border: 1px solid rgba(255, 255, 255, 0.12);
                    border-top: 1px solid rgba(255, 255, 255, 0.25);
                }
                QPushButton:hover {
                    background-color: #1084d9;
                    border-color: rgba(255, 255, 255, 0.32);
                }
                QPushButton:pressed {
                    background-color: #0067b8;
                    border-color: rgba(255, 255, 255, 0.08);
                }
                QPushButton:disabled {
                    background-color: #2a2a2a;
                    color: #666666;
                    border: 1px solid rgba(255, 255, 255, 0.05);
                }
            """)
            self.timer_label.setText("00:00:00")
            self.timer_label.setStyleSheet("""
                QLabel {
                    background-color: #262626;
                    color: #ffffff;
                    font-family: 'Segoe UI Variable Display', 'Segoe UI', 'Cascadia Mono', monospace;
                    font-size: 15px;
                    font-weight: 600;
                    padding: 4px 16px;
                    border-radius: 6px;
                    border: 1px solid rgba(255, 255, 255, 0.08);
                }
            """)
            if hasattr(self, 'level_bar'):
                self.level_bar.setValue(0)

    def _on_audio_level(self, level: float) -> None:
        if self.recorder and getattr(self.recorder, 'is_paused', False):
            self.level_bar.setValue(0)
            return
        percent = int(level * 100)
        self.level_bar.setValue(percent)

    def _on_audio_error(self, message: str) -> None:
        self._stop_recording()
        QMessageBox.critical(self, "Ошибка аудио", message)

    # ===============================================================
    # Таблица истории (ТЗ п. 3, чек-лист п. 8)
    # ===============================================================
    def _refresh_history(self) -> None:
        recordings = self.db.get_all_recordings()
        self.history_table.setRowCount(len(recordings))

        for row, rec in enumerate(recordings):
            # 0: Дата
            item_date = QTableWidgetItem(str(rec.get('created_at', '')))
            item_date.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.history_table.setItem(row, 0, item_date)

            # 1: Тема
            item_title = QTableWidgetItem(rec.get('title') or "Без названия")
            self.history_table.setItem(row, 1, item_title)

            # 2: Длительность
            dur = rec.get('duration', 0.0) or 0.0
            mins = int(dur // 60)
            secs = int(dur % 60)
            item_dur = QTableWidgetItem(f"{mins:02d}:{secs:02d}")
            item_dur.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.history_table.setItem(row, 2, item_dur)

            # 3: Размер
            size_kb = (rec.get('file_size', 0) or 0) / 1024
            size_str = f"{size_kb / 1024:.1f} МБ" if size_kb > 1024 else f"{size_kb:.0f} КБ"
            item_size = QTableWidgetItem(size_str)
            item_size.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.history_table.setItem(row, 3, item_size)

            # 4: Статус отправки («Отправлено» / «Не отправлено» / «В процессе»)
            raw_status = rec.get('status') or STATUS_NOT_SENT
            if raw_status in ("Локально", "Ошибка"):
                raw_status = STATUS_NOT_SENT

            item_status = QTableWidgetItem(raw_status)
            item_status.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

            if raw_status == STATUS_SENT:
                item_status.setForeground(QColor("#2e7d32"))  # Green
            elif raw_status == STATUS_NOT_SENT:
                item_status.setForeground(QColor("#c62828"))  # Red
            elif raw_status == STATUS_IN_PROGRESS:
                item_status.setForeground(QColor("#ef6c00"))  # Orange

            self.history_table.setItem(row, 4, item_status)

            # 5: Действия
            # Возле каждой строки:
            # - Открыть папку с файлом на ПК (через кроссплатформенный вызов).
            # - Кнопка «Повторить отправку» (активна ТОЛЬКО для записей со статусом «Не отправлено»).
            action_widget = QWidget()
            action_layout = QHBoxLayout(action_widget)
            action_layout.setContentsMargins(2, 2, 2, 2)
            action_layout.setSpacing(4)

            folder_btn = QPushButton("Папка")
            folder_btn.setToolTip("Показать файл в папке на диске")
            folder_btn.setStyleSheet("""
                QPushButton {
                    background-color: #2d2d2d;
                    color: #d4d4d4;
                    font-family: 'Segoe UI Variable Text', 'Segoe UI', sans-serif;
                    font-size: 12px;
                    border-radius: 5px;
                    border: 1px solid rgba(255, 255, 255, 0.08);
                    padding: 3px 8px;
                }
                QPushButton:hover {
                    background-color: #383838;
                    color: #ffffff;
                    border-color: rgba(255, 255, 255, 0.16);
                }
                QPushButton:pressed {
                    background-color: #242424;
                }
            """)
            folder_btn.clicked.connect(lambda _, fp=rec['filepath']: self._show_in_folder(fp))
            action_layout.addWidget(folder_btn)

            retry_btn = QPushButton("Повторить отправку")
            retry_btn.setToolTip("Повторно отправить запись WebM и JSON на сервер")
            retry_btn.setStyleSheet("""
                QPushButton {
                    background-color: #2d2d2d;
                    color: #d4d4d4;
                    font-family: 'Segoe UI Variable Text', 'Segoe UI', sans-serif;
                    font-size: 12px;
                    border-radius: 5px;
                    border: 1px solid rgba(255, 255, 255, 0.08);
                    padding: 3px 8px;
                }
                QPushButton:hover {
                    background-color: #383838;
                    color: #ffffff;
                    border-color: rgba(255, 255, 255, 0.16);
                }
                QPushButton:pressed {
                    background-color: #242424;
                }
                QPushButton:disabled {
                    background-color: #1f1f1f;
                    color: #555555;
                    border-color: rgba(255, 255, 255, 0.04);
                }
            """)

            # Кнопка активна ТОЛЬКО для записей со статусом «Не отправлено» (ТЗ п. 3)
            if raw_status == STATUS_NOT_SENT:
                retry_btn.setEnabled(True)
                j_fp = rec.get('json_filepath') or str(Path(rec['filepath']).with_suffix('.json'))
                retry_btn.clicked.connect(
                    lambda _, r_id=rec['id'], a_fp=rec['filepath'], j_p=j_fp: self._trigger_upload(r_id, a_fp, j_p)
                )
            else:
                retry_btn.setEnabled(False)

            action_layout.addWidget(retry_btn)

            play_btn = QPushButton("▶")
            play_btn.setToolTip("Воспроизвести запись")
            play_btn.setFixedWidth(32)
            play_btn.setFixedHeight(26)
            play_btn.setStyleSheet("""
                QPushButton {
                    background-color: #2d2d2d;
                    color: #60cdff;
                    font-family: 'Segoe UI Variable Text', 'Segoe UI', sans-serif;
                    font-size: 12px;
                    font-weight: bold;
                    border-radius: 5px;
                    border: 1px solid rgba(255, 255, 255, 0.08);
                }
                QPushButton:hover {
                    background-color: #0078d4;
                    color: #ffffff;
                    border-color: #0078d4;
                }
                QPushButton:pressed {
                    background-color: #005a9e;
                }
            """)
            play_btn.clicked.connect(lambda _, fp=rec['filepath']: self._play_audio(fp))
            action_layout.addWidget(play_btn)

            self.history_table.setCellWidget(row, 5, action_widget)

    def _play_audio(self, filepath: str) -> None:
        path = Path(filepath)
        if not path.exists():
            QMessageBox.warning(self, "Файл не найден", f"Файл не найден:\n{filepath}")
            return
        try:
            if sys.platform == "win32":
                os.startfile(str(path))
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except Exception as e:
            QMessageBox.warning(self, "Ошибка", f"Не удалось открыть файл:\n{e}")

    def _show_in_folder(self, filepath: str) -> None:
        path = Path(filepath)
        if not path.exists():
            self._open_recordings_folder()
            return
        try:
            if sys.platform == "win32":
                subprocess.run(f'explorer /select,"{str(path)}"', shell=True)
            elif sys.platform == "darwin":
                subprocess.run(["open", "-R", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path.parent)])
        except Exception as e:
            self._open_recordings_folder()

    def _open_recordings_folder(self) -> None:
        path = Path(self.config.recordings_dir)
        path.mkdir(parents=True, exist_ok=True)
        try:
            if sys.platform == "win32":
                os.startfile(str(path))
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except Exception as e:
            QMessageBox.warning(self, "Ошибка", f"Не удалось открыть папку:\n{e}")
