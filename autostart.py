import os
import sys
from pathlib import Path

APP_NAME = "AudioDictaphone"

def set_autostart(enable: bool = True) -> bool:
    """
    Cross-platform autostart configuration.
    Windows: Registry Run key
    Linux: ~/.config/autostart/ desktop file
    """
    executable = sys.executable
    if getattr(sys, 'frozen', False):
        app_path = sys.executable
    else:
        app_path = f'"{executable}" "{Path(__file__).resolve().parent / "main.py"}"'

    if sys.platform == "win32":
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0,
                winreg.KEY_SET_VALUE
            )
            if enable:
                winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, app_path)
            else:
                try:
                    winreg.DeleteValue(key, APP_NAME)
                except FileNotFoundError:
                    pass
            winreg.CloseKey(key)
            return True
        except Exception as e:
            print(f"[Autostart] Windows error: {e}")
            return False

    elif sys.platform.startswith("linux"):
        try:
            autostart_dir = Path.home() / ".config" / "autostart"
            autostart_dir.mkdir(parents=True, exist_ok=True)
            desktop_file = autostart_dir / f"{APP_NAME}.desktop"

            if enable:
                content = f"""[Desktop Entry]
Type=Application
Exec={app_path}
Hidden=false
NoDisplay=false
X-GNOME-Autostart-enabled=true
Name={APP_NAME}
Comment=Audio Recording Dictaphone Service
"""
                with open(desktop_file, "w", encoding="utf-8") as f:
                    f.write(content)
            else:
                if desktop_file.exists():
                    desktop_file.unlink()
            return True
        except Exception as e:
            print(f"[Autostart] Linux error: {e}")
            return False

    return False

def is_autostart_enabled() -> bool:
    """Check if autostart is currently enabled."""
    if sys.platform == "win32":
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0,
                winreg.KEY_READ
            )
            try:
                winreg.QueryValueEx(key, APP_NAME)
                return True
            except FileNotFoundError:
                return False
            finally:
                winreg.CloseKey(key)
        except Exception:
            return False

    elif sys.platform.startswith("linux"):
        desktop_file = Path.home() / ".config" / "autostart" / f"{APP_NAME}.desktop"
        return desktop_file.exists()

    return False
