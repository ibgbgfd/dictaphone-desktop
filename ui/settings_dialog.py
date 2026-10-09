from pathlib import Path
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QSpinBox, QCheckBox, QPushButton, QFileDialog, QGroupBox,
    QTabWidget, QWidget, QMessageBox, QComboBox
)
from PyQt6.QtCore import Qt

from config import AppConfig, save_config
from audio_recorder import AudioRecorder
import autostart

class SettingsDialog(QDialog):
    def __init__(self, config: AppConfig, parent=None):
        super().__init__(parent)
        self.config = config
        self.setWindowTitle("Настройки")
        self.resize(520, 420)
        self.setModal(True)

        self._init_ui()
        self._load_values()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(12, 12, 12, 12)

        # Tab widget for organizing settings
        self.tabs = QTabWidget()

        # Tab 1: Основные и система
        general_tab = QWidget()
        gen_layout = QVBoxLayout(general_tab)
        gen_layout.setSpacing(10)

        sys_group = QGroupBox("Системная интеграция")
        sys_layout = QVBoxLayout(sys_group)
        self.autostart_check = QCheckBox("Автозагрузка при старте Windows")
        self.start_minimized_check = QCheckBox("Запускать свернутым в трей")
        self.tray_check = QCheckBox("Сворачивать в системный трей при закрытии окна")
        sys_layout.addWidget(self.autostart_check)
        sys_layout.addWidget(self.start_minimized_check)
        sys_layout.addWidget(self.tray_check)
        gen_layout.addWidget(sys_group)

        # Storage group
        storage_group = QGroupBox("Хранилище и ротация записей")
        storage_layout = QVBoxLayout(storage_group)
        
        path_label = QLabel("Папка для сохранения аудиозаписей (WebM):")
        storage_layout.addWidget(path_label)

        path_row = QHBoxLayout()
        self.dir_input = QLineEdit()
        self.browse_btn = QPushButton("Обзор...")
        self.browse_btn.clicked.connect(self._browse_dir)
        path_row.addWidget(self.dir_input, 1)
        path_row.addWidget(self.browse_btn)
        storage_layout.addLayout(path_row)

        days_row = QHBoxLayout()
        days_row.addWidget(QLabel("Срок хранения записей (дней):"))
        self.days_spin = QSpinBox()
        self.days_spin.setRange(1, 365)
        self.days_spin.setToolTip("Файлы и записи в БД старше этого срока удаляются при старте и перед новой записью")
        days_row.addWidget(self.days_spin)
        days_row.addStretch()
        storage_layout.addLayout(days_row)

        gen_layout.addWidget(storage_group)
        gen_layout.addStretch()
        self.tabs.addTab(general_tab, "Основные")

        # Tab 2: Сеть и сервер (HTTP / POST)
        net_tab = QWidget()
        net_layout = QVBoxLayout(net_tab)
        net_layout.setSpacing(10)

        # HTTP Server group
        http_group = QGroupBox("Порт")
        http_layout = QVBoxLayout(http_group)

        http_port_row = QHBoxLayout()
        http_port_row.addWidget(QLabel("Порт:"))
        self.http_port_spin = QSpinBox()
        self.http_port_spin.setRange(1024, 65535)
        http_port_row.addWidget(self.http_port_spin)
        http_port_row.addStretch()
        http_layout.addLayout(http_port_row)

        net_layout.addWidget(http_group)

        # HTTP POST group
        upload_group = QGroupBox("Отправка данных (HTTP / HTTPS POST multipart/form-data)")
        upload_layout = QVBoxLayout(upload_group)

        self.auto_upload_check = QCheckBox("Автоматически отправлять запись на сервер после остановки")
        upload_layout.addWidget(self.auto_upload_check)

        url_label = QLabel("URL сервера для выгрузки WebM и JSON:")
        upload_layout.addWidget(url_label)
        self.upload_url_input = QLineEdit()
        self.upload_url_input.textChanged.connect(self._update_protocol_badge)
        upload_layout.addWidget(self.upload_url_input)

        self.protocol_badge = QLabel()
        self.protocol_badge.setStyleSheet("padding: 4px 8px; border-radius: 4px; font-weight: bold; font-size: 11px;")
        upload_layout.addWidget(self.protocol_badge)

        self.verify_ssl_check = QCheckBox("Строгая проверка SSL-сертификатов (снимите для самоподписанных / локальных IP)")
        upload_layout.addWidget(self.verify_ssl_check)

        retries_row = QHBoxLayout()
        retries_row.addWidget(QLabel("Количество повторных попыток при сбое сети:"))
        self.retries_spin = QSpinBox()
        self.retries_spin.setRange(1, 10)
        retries_row.addWidget(self.retries_spin)
        retries_row.addStretch()
        upload_layout.addLayout(retries_row)

        net_layout.addWidget(upload_group)
        net_layout.addStretch()
        self.tabs.addTab(net_tab, "Сеть и интеграция")

        # Tab 3: Аудио и микрофон
        audio_tab = QWidget()
        audio_layout = QVBoxLayout(audio_tab)
        audio_layout.setSpacing(10)

        # Microphone device group
        mic_group = QGroupBox("Устройство записи (Микрофон)")
        mic_layout = QVBoxLayout(mic_group)
        mic_layout.setSpacing(8)

        mic_label = QLabel("Активный микрофон для записи мероприятий:")
        mic_layout.addWidget(mic_label)

        mic_row = QHBoxLayout()
        self.device_combo = QComboBox()
        self.refresh_devices_btn = QPushButton("Обновить")
        self.refresh_devices_btn.clicked.connect(self._load_devices)
        mic_row.addWidget(self.device_combo, 1)
        mic_row.addWidget(self.refresh_devices_btn)
        mic_layout.addLayout(mic_row)

        audio_layout.addWidget(mic_group)

        codec_group = QGroupBox("Параметры кодирования WebM (Opus)")
        codec_layout = QVBoxLayout(codec_group)

        rate_row = QHBoxLayout()
        rate_row.addWidget(QLabel("Частота дискретизации:"))
        self.rate_combo = QComboBox()
        self.rate_combo.addItem("48000 Гц (стандарт Opus)", 48000)
        self.rate_combo.addItem("44100 Гц", 44100)
        self.rate_combo.addItem("16000 Гц (речь)", 16000)
        rate_row.addWidget(self.rate_combo)
        rate_row.addStretch()
        codec_layout.addLayout(rate_row)

        bitrate_row = QHBoxLayout()
        bitrate_row.addWidget(QLabel("Битрейт аудио:"))
        self.bitrate_combo = QComboBox()
        self.bitrate_combo.addItem("32 кбит/с (компактный)", 32000)
        self.bitrate_combo.addItem("64 кбит/с (рекомендуемый для речи)", 64000)
        self.bitrate_combo.addItem("96 кбит/с (высокое качество)", 96000)
        self.bitrate_combo.addItem("128 кбит/с (максимальное)", 128000)
        bitrate_row.addWidget(self.bitrate_combo)
        bitrate_row.addStretch()
        codec_layout.addLayout(bitrate_row)

        audio_layout.addWidget(codec_group)
        audio_layout.addStretch()
        self.tabs.addTab(audio_tab, "Аудио")

        layout.addWidget(self.tabs)

        # Dialog Buttons
        btn_box = QHBoxLayout()
        btn_box.addStretch()
        
        self.save_btn = QPushButton("Сохранить")
        self.save_btn.setDefault(True)
        self.save_btn.clicked.connect(self._save_and_close)
        
        self.cancel_btn = QPushButton("Отмена")
        self.cancel_btn.clicked.connect(self.reject)

        btn_box.addWidget(self.save_btn)
        btn_box.addWidget(self.cancel_btn)
        layout.addLayout(btn_box)

    def _load_values(self) -> None:
        self.autostart_check.setChecked(autostart.is_autostart_enabled())
        self.start_minimized_check.setChecked(self.config.start_minimized)
        self.tray_check.setChecked(self.config.minimize_to_tray)
        self.dir_input.setText(self.config.recordings_dir)
        self.days_spin.setValue(self.config.max_storage_days)
        
        self.http_port_spin.setValue(self.config.http_port)
        self.auto_upload_check.setChecked(getattr(self.config, 'auto_upload', True))
        self.upload_url_input.setText(self.config.upload_url)
        self.verify_ssl_check.setChecked(getattr(self.config, 'verify_ssl', False))
        self.retries_spin.setValue(self.config.max_upload_retries)

        # Sample rate
        for i in range(self.rate_combo.count()):
            if self.rate_combo.itemData(i) == self.config.sample_rate:
                self.rate_combo.setCurrentIndex(i)
                break

        # Bitrate
        for i in range(self.bitrate_combo.count()):
            if self.bitrate_combo.itemData(i) == self.config.bitrate:
                self.bitrate_combo.setCurrentIndex(i)
                break

        # Microphone device
        self._load_devices()

        self._update_protocol_badge()

    def _load_devices(self) -> None:
        self.device_combo.blockSignals(True)
        self.device_combo.clear()

        devices = AudioRecorder.get_input_devices()
        selected_index = 0

        for i, dev in enumerate(devices):
            self.device_combo.addItem(dev['name'], dev['index'])
            if self.config.device_index >= 0 and dev['index'] == self.config.device_index:
                selected_index = i
            elif self.config.device_index < 0 and dev.get('is_default'):
                selected_index = i

        if devices:
            self.device_combo.setCurrentIndex(selected_index)
        else:
            self.device_combo.addItem("Микрофон не обнаружен", -1)

        self.device_combo.blockSignals(False)

    def _update_protocol_badge(self) -> None:
        url = self.upload_url_input.text().strip().lower()
        if url.startswith("https://"):
            self.protocol_badge.setText("🔒 Протокол: Защищенный HTTPS (SSL/TLS шифрование)")
            self.protocol_badge.setStyleSheet(
                "background-color: #064e3b; color: #6ee7b7; border: 1px solid #059669; padding: 4px 8px; border-radius: 4px; font-weight: bold; font-size: 11px;"
            )
        elif url.startswith("http://"):
            self.protocol_badge.setText("🌐 Протокол: Стандартный HTTP (локальная сеть / без SSL)")
            self.protocol_badge.setStyleSheet(
                "background-color: #1e293b; color: #94a3b8; border: 1px solid #475569; padding: 4px 8px; border-radius: 4px; font-weight: bold; font-size: 11px;"
            )
        else:
            self.protocol_badge.setText("⚠️ Протокол не указан (будет определен автоматически)")
            self.protocol_badge.setStyleSheet(
                "background-color: #451a03; color: #fdba74; border: 1px solid #d97706; padding: 4px 8px; border-radius: 4px; font-weight: bold; font-size: 11px;"
            )


    def _browse_dir(self) -> None:
        curr = self.dir_input.text()
        chosen = QFileDialog.getExistingDirectory(self, "Выберите папку для записей", curr)
        if chosen:
            self.dir_input.setText(chosen)

    def _save_and_close(self) -> None:
        # Apply autostart
        new_autostart = self.autostart_check.isChecked()
        if new_autostart != autostart.is_autostart_enabled():
            ok = autostart.set_autostart(new_autostart)
            if not ok:
                QMessageBox.warning(self, "Автозапуск", "Не удалось изменить параметры автозапуска в системе.")
        self.config.autostart_enabled = new_autostart

        self.config.start_minimized = self.start_minimized_check.isChecked()
        self.config.minimize_to_tray = self.tray_check.isChecked()
        
        # Storage
        rec_dir = self.dir_input.text().strip()
        if rec_dir:
            Path(rec_dir).mkdir(parents=True, exist_ok=True)
            self.config.recordings_dir = rec_dir
        self.config.max_storage_days = self.days_spin.value()

        # Network
        self.config.http_port = self.http_port_spin.value()
        self.config.auto_upload = self.auto_upload_check.isChecked()
        self.config.upload_url = self.upload_url_input.text().strip()
        self.config.verify_ssl = self.verify_ssl_check.isChecked()
        self.config.max_upload_retries = self.retries_spin.value()

        # Audio
        dev_idx = self.device_combo.currentData()
        if dev_idx is not None:
            self.config.device_index = dev_idx

        chosen_rate = self.rate_combo.currentData()
        if chosen_rate:
            self.config.sample_rate = chosen_rate

        chosen_bitrate = self.bitrate_combo.currentData()
        if chosen_bitrate:
            self.config.bitrate = chosen_bitrate

        save_config(self.config)
        self.accept()
