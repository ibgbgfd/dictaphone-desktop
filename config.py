import os
import sys
import json
from pathlib import Path
from dataclasses import dataclass, asdict

def get_app_data_dir() -> Path:
    """Returns standard OS application data directory."""
    if sys.platform == "win32":
        app_data = os.getenv("APPDATA")
        base = Path(app_data) if app_data else (Path.home() / "AppData" / "Roaming")
        target = base / "AudioDictaphone"
    elif sys.platform == "darwin":
        target = Path.home() / "Library" / "Application Support" / "AudioDictaphone"
    else:
        # Linux standard (XDG_DATA_HOME or ~/.local/share)
        xdg_data = os.getenv("XDG_DATA_HOME")
        base = Path(xdg_data) if xdg_data else (Path.home() / ".local" / "share")
        target = base / "audio_dictaphone"

    target.mkdir(parents=True, exist_ok=True)
    return target


APP_DATA_DIR = get_app_data_dir()
DEFAULT_RECORDINGS_DIR = APP_DATA_DIR / "recordings"
CONFIG_FILE = APP_DATA_DIR / "config.json"


@dataclass
class AppConfig:
    # Audio Settings
    sample_rate: int = 48000
    channels: int = 1
    audio_codec: str = "libopus"  # PyAV opus encoder
    bitrate: int = 64000         # 64 kbps (ideal for speech)
    device_index: int = -1       # -1 means system default
    
    # Network / Server Settings (HTTP REST API and POST upload)
    http_host: str = "0.0.0.0"
    http_port: int = 8765
    upload_url: str = "http://127.0.0.1:5678/webhook-test/test"
    auto_upload: bool = True
    max_upload_retries: int = 5
    verify_ssl: bool = False  # Поддержка HTTPS: True - проверять сертификаты, False - разрешать самоподписанные SSL
    
    # Storage Settings
    recordings_dir: str = str(DEFAULT_RECORDINGS_DIR)
    max_storage_days: int = 30
    
    # System Integration
    minimize_to_tray: bool = True
    start_minimized: bool = False
    autostart_enabled: bool = False

    @property
    def ws_host(self) -> str:
        return self.http_host

    @ws_host.setter
    def ws_host(self, val: str):
        self.http_host = val

    @property
    def ws_port(self) -> int:
        return self.http_port

    @ws_port.setter
    def ws_port(self, val: int):
        self.http_port = val


def load_config() -> AppConfig:
    config = AppConfig()
    local_cfg = Path(__file__).resolve().parent / "config.json"
    cfg_to_read = CONFIG_FILE if CONFIG_FILE.exists() else (local_cfg if local_cfg.exists() else None)

    if cfg_to_read and cfg_to_read.exists():
        try:
            with open(cfg_to_read, "r", encoding="utf-8") as f:
                data = json.load(f)
                # Автоматическая миграция ws_host / ws_port -> http_host / http_port
                if "ws_host" in data and "http_host" not in data:
                    data["http_host"] = data["ws_host"]
                if "ws_port" in data and "http_port" not in data:
                    data["http_port"] = data["ws_port"]
                for k, v in data.items():
                    if hasattr(config, k):
                        setattr(config, k, v)
        except Exception as e:
            print(f"[Config] Error loading config file: {e}")

    # Ensure recordings_dir defaults to standard AppData directory
    if not config.recordings_dir or "Downloads" in config.recordings_dir:
        config.recordings_dir = str(DEFAULT_RECORDINGS_DIR)

    Path(config.recordings_dir).mkdir(parents=True, exist_ok=True)
    save_config(config)
    return config


def save_config(config: AppConfig) -> None:
    try:
        Path(CONFIG_FILE).parent.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(asdict(config), f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[Config] Error saving config file: {e}")
