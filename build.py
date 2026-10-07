import os
import sys
from pathlib import Path
import PyInstaller.__main__

BASE_DIR = Path(__file__).resolve().parent

def build():
    print("[Build] Запуск сборки приложения Диктофон...")
    icon_path = BASE_DIR / "resources" / "icon.ico"
    resources_path = BASE_DIR / "resources"

    args = [
        str(BASE_DIR / "main.py"),
        "--noconfirm",
        "--onefile",
        "--windowed",
        "--name=Dictaphone",
        f"--icon={icon_path}",
        f"--add-data={resources_path};resources",
        "--collect-all=sounddevice",
        "--collect-all=av",
        "--hidden-import=PyQt6",
        "--hidden-import=requests",
        "--hidden-import=session_validator",
    ]

    print("[Build] Параметры PyInstaller:", " ".join(args))
    PyInstaller.__main__.run(args)
    print("\n[Build] Сборка успешно завершена! Исполняемый файл: dist/Dictaphone.exe")

if __name__ == "__main__":
    build()
