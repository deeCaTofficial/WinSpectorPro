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
from pathlib import Path  # Используем pathlib для надежной работы с путями
from typing import Dict, NoReturn

# --- 1. Константы и флаги ---

IS_FROZEN = getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS')
MIN_PYTHON_VERSION = (3, 10)


# --- 2. Функции проверки и аварийного логирования ---

def _show_critical_error_message(title: str, message: str) -> None:
    """Пытается показать ошибку в GUI, если это возможно."""
    try:
        from PyQt6.QtWidgets import QApplication, QMessageBox
        # Убедимся, что QApplication существует, чтобы не создавать его лишний раз
        app = QApplication.instance() or QApplication(sys.argv)
        QMessageBox.critical(None, title, message)
    except ImportError:
        # Если PyQt6 не установлен или не может быть импортирован,
        # просто выводим сообщение в консоль.
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
    Используется для отлова самых ранних сбоев (например, при импортах).
    """
    try:
        # Создаем лог в папке пользователя, чтобы избежать проблем с правами записи
        log_dir = Path.home() / ".winspector"
        log_dir.mkdir(exist_ok=True)
        
        log_file = log_dir / "winspector_crash.log"
        with log_file.open("a", encoding="utf-8") as f:
            f.write(f"--- CRASH AT {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ---\n")
            f.write(error_message + "\n\n")
    except Exception as e:
        # Если не удалось даже это, выводим в консоль
        print(f"Не удалось записать аварийный лог: {e}", file=sys.stderr)
        print(f"Оригинальная ошибка:\n{error_message}", file=sys.stderr)


# --- 3. Основная функция-лаунчер ---

def run_app() -> NoReturn:
    """
    Главная функция-лаунчер.
    Настраивает окружение и передает управление основному приложению.
    """
    # Сначала проверяем окружение
    check_environment()
    
    app_paths: Dict[str, Path]
    
    try:
        # Определяем пути в зависимости от режима запуска
        if IS_FROZEN:
            # Мы в .exe, собранном PyInstaller.
            # Базовый путь - это временная папка, куда PyInstaller распаковал все.
            # Файлы данных (как knowledge_base.yaml) будут лежать здесь же.
            base_path = Path(sys._MEIPASS)
            # Путь к папке логов лучше делать рядом с .exe, а не во временной папке
            exe_dir = Path(sys.executable).parent
            log_dir = exe_dir / 'logs'
        else:
            # Мы работаем из исходников .py
            # __file__ -> .../src/main.py -> .parent -> .../src/
            base_path = Path(__file__).parent.resolve()
            # Папка logs в корне проекта, на уровень выше папки 'src'
            log_dir = base_path.parent / 'logs'

        # Добавляем корневую папку 'src' в системный путь, если работаем из исходников.
        # Это не нужно для .exe, так как PyInstaller сам управляет путями.
        if not IS_FROZEN:
            src_root = str(base_path.parent)
            if src_root not in sys.path:
                sys.path.insert(0, src_root)
        
        # Импортируем основной модуль здесь, после настройки путей.
        # Это защищает от ImportError, если структура проекта неправильная.
        from src.winspector.application import main as app_main

        # Создаем словарь с путями, который будет передан в основное приложение
        app_paths = {
            "base": base_path, # Путь для поиска данных (knowledge_base.yaml)
            "logs": log_dir      # Путь для записи логов
        }
        
        # Передаем управление и код выхода из приложения обратно в систему
        sys.exit(app_main(app_paths))

    except Exception:
        # Ловим самые ранние ошибки, например, сбой импорта `src.winspector.application`
        full_error_message = f"Критическая ошибка на этапе запуска:\n{traceback.format_exc()}"
        
        emergency_log(full_error_message)
        
        _show_critical_error_message(
            "Критическая ошибка запуска",
            "Не удалось запустить приложение из-за непредвиденной ошибки.\n\n"
            "Подробности были записаны в файл 'winspector_crash.log' в вашей домашней папке."
        )
        
        sys.exit(1)


if __name__ == "__main__":
    # Эта проверка гарантирует, что код будет выполнен только при запуске этого файла,
    # а не при его импорте.
    run_app()