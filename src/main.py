# src/main.py
"""
Главная точка входа и "лаунчер" для приложения WinSpector Pro.

Задачи этого файла:
1.  Проверить совместимость окружения (ОС, версия Python).
2.  Настроить "аварийное" логирование на случай сбоев при импорте.
3.  Определить базовые пути для работы приложения, учитывая,
    запущено оно из исходников или как собранный .exe (PyInstaller).
4.  Передать управление основному модулю приложения.
"""
import sys
import os
import traceback
from datetime import datetime
from pathlib import Path
from typing import Dict, NoReturn
import multiprocessing

# --- 1. Константы и флаги ---

IS_FROZEN = getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS')
MIN_PYTHON_VERSION = (3, 10)


# --- 2. Функции проверки и аварийного логирования ---

def _show_critical_error_message(title: str, message: str) -> None:
    """Пытается показать ошибку в GUI, если это возможно."""
    try:
        from PyQt6.QtWidgets import QApplication, QMessageBox
        app = QApplication.instance() or QApplication(sys.argv)
        QMessageBox.critical(None, title, message)
    except ImportError:
        print(f"Критическая ошибка: {title}\n{message}", file=sys.stderr)

def check_environment() -> None:
    """Проверяет, подходит ли текущее окружение для запуска."""
    if sys.platform != "win32":
        _show_critical_error_message(
            "Ошибка совместимости",
            "WinSpector Pro предназначен для работы только на операционных системах Windows."
        )
        sys.exit(1)
        
    if sys.version_info < MIN_PYTHON_VERSION:
        error_msg = (f"Требуется Python версии {'.'.join(map(str, MIN_PYTHON_VERSION))} или выше.\n"
                     f"Ваша версия: {sys.version.split(' ')[0]}")
        _show_critical_error_message("Ошибка версии Python", error_msg)
        sys.exit(1)

def emergency_log(error_message: str) -> None:
    """
    Записывает критическую ошибку в файл, если основной логгер еще не работает.
    """
    try:
        log_dir = Path.home() / ".winspector"
        log_dir.mkdir(exist_ok=True)
        
        log_file = log_dir / "winspector_crash.log"
        with log_file.open("a", encoding="utf-8") as f:
            f.write(f"--- CRASH AT {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ---\n")
            f.write(error_message + "\n\n")
    except Exception as e:
        print(f"Не удалось записать аварийный лог: {e}", file=sys.stderr)
        print(f"Оригинальная ошибка:\n{error_message}", file=sys.stderr)


# --- 3. Основная функция-лаунчер ---

def run_app() -> NoReturn:
    """
    Главная функция-лаунчер.
    Настраивает окружение и передает управление основному приложению.
    """
    check_environment()
    
    app_paths: Dict[str, Path]
    
    try:
        if IS_FROZEN:
            # Режим .exe
            base_path = Path(sys._MEIPASS)
            log_dir = Path(sys.executable).parent / 'logs'
            assets_dir = base_path / 'assets'
            # Путь к данным внутри .exe
            kb_path = base_path / 'winspector' / 'data' / 'knowledge_base'
        else:
            # Режим разработки
            base_path = Path(__file__).parent.resolve() # -> C:/.../WinSpector_Pro_v1.0.0/src
            log_dir = base_path.parent / 'logs'
            assets_dir = base_path.parent / 'assets'
            
            # ####################################################################
            # ### ФИНАЛЬНОЕ ИСПРАВЛЕНИЕ ЗДЕСЬ ###
            # ####################################################################
            # Строим путь: src -> winspector -> data -> knowledge_base
            kb_path = base_path / 'winspector' / 'data' / 'knowledge_base'

        if not IS_FROZEN:
            src_root = str(base_path.parent)
            if src_root not in sys.path:
                sys.path.insert(0, src_root)
        
        from src.winspector.application import main as app_main

        app_paths = {
            "base": base_path,
            "logs": log_dir,
            "assets": assets_dir,
            "kb_path": kb_path,
        }
        
        sys.exit(app_main(app_paths))

    except Exception:
        full_error_message = f"Критическая ошибка на этапе запуска:\n{traceback.format_exc()}"
        emergency_log(full_error_message)
        _show_critical_error_message(
            "Критическая ошибка запуска",
            "Не удалось запустить приложение из-за непредвиденной ошибки.\n\n"
            "Подробности были записаны в файл 'winspector_crash.log' в вашей домашней папке."
        )
        sys.exit(1)

if __name__ == "__main__":
    multiprocessing.freeze_support()
    run_app()