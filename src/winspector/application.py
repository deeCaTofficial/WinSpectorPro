# src/winspector/application.py
"""
Основной модуль приложения WinSpector Pro.

Отвечает за всю логику запуска:
- Настройка детального логирования.
- Проверка прав администратора и защита от повторного запуска.
- Обработка необработанных исключений (глобальный crash handler).
- Создание и запуск QApplication, асинхронного цикла и главного окна.
"""

import sys
import os
import ctypes
from ctypes import wintypes
import logging
import asyncio
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Dict

# --- 1. Импорт зависимостей ---
try:
    from PyQt6.QtWidgets import QApplication, QMessageBox
    from PyQt6.QtCore import QSharedMemory
    from PyQt6.QtGui import QIcon
    from dotenv import load_dotenv
    import qasync
except ImportError as e:
    print(f"КРИТИЧЕСКАЯ ОШИБКА: Не найдены основные зависимости. "
          f"Пожалуйста, установите их командой 'pip install -r requirements.txt'.\nОшибка: {e}", file=sys.stderr)
    sys.exit(1)

# Загружаем переменные окружения из .env файла в самом начале
load_dotenv()

# --- Импорты из нашего проекта ---
try:
    from src.winspector import APP_NAME, ORG_NAME, APP_VERSION
    from src.winspector.core import WinSpectorCore
    from src.winspector.gui import MainWindow
    from src.winspector.resources import assets_rc
except ImportError as e:
    print(f"КРИТИЧЕСКАЯ ОШИБКА: Не удалось импортировать модули проекта. "
          f"Убедитесь, что вы запускаете приложение из корневой папки проекта.\nОшибка: {e}", file=sys.stderr)
    sys.exit(1)

# --- Глобальные переменные и настройки ---
logger = logging.getLogger(__name__)
original_hook = sys.excepthook

# --- 4. Функции-помощники ---

def setup_logging(log_dir: Path) -> None:
    """Настраивает глобальное логирование в файл и консоль."""
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / 'winspector.log'
        file_handler = RotatingFileHandler(
            log_path, maxBytes=2*1024*1024, backupCount=5, encoding='utf-8'
        )
        file_handler.setFormatter(
            logging.Formatter('%(asctime)s - %(levelname)s - [%(name)s:%(lineno)d] - %(message)s')
        )
        file_handler.setLevel(logging.INFO)
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(logging.Formatter('[%(levelname)s] %(message)s'))
        console_handler.setLevel(logging.DEBUG)
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.DEBUG)
        if root_logger.hasHandlers():
            root_logger.handlers.clear()
        root_logger.addHandler(file_handler)
        root_logger.addHandler(console_handler)
        logging.getLogger("winspector.init").info(
            f"Система логирования для {APP_NAME} v{APP_VERSION} инициализирована."
        )
    except Exception as e:
        QMessageBox.critical(None, "Ошибка логирования", f"Не удалось настроить запись логов: {e}")

def handle_exception(exc_type, exc, tb) -> None:
    """Глобальный обработчик необработанных исключений (crash handler)."""
    if issubclass(exc_type, KeyboardInterrupt):
        if original_hook:
            original_hook(exc_type, exc, tb)
        return
    logger.critical("Перехвачено необработанное исключение:", exc_info=(exc_type, exc, tb))
    app = QApplication.instance()
    is_shutting_down = not app or app.property("is_shutting_down")
    if not is_shutting_down:
        try:
            msg_box = QMessageBox()
            msg_box.setIcon(QMessageBox.Icon.Critical)
            msg_box.setText("Произошла критическая ошибка.")
            msg_box.setInformativeText(f"{str(exc)}\n\nПодробности в файле winspector.log.")
            msg_box.setWindowTitle("Ошибка")
            msg_box.setStandardButtons(QMessageBox.StandardButton.Ok)
            msg_box.exec()
        except Exception as e:
            logger.critical(f"Не удалось создать QMessageBox для отображения ошибки: {e}")
    if original_hook:
        original_hook(exc_type, exc, tb)
    if QApplication.instance():
        QApplication.instance().quit()

def check_admin_rights() -> bool:
    """Проверяет, запущено ли приложение с правами администратора."""
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except (AttributeError, OSError):
        return False

def relaunch_as_admin() -> None:
    """Пытается перезапустить приложение с правами администратора."""
    logger.info("Отправлен запрос на перезапуск с правами администратора.")
    try:
        ctypes.windll.shell32.ShellExecuteW(
            None, "runas", sys.executable, " ".join(sys.argv), None, 1
        )
    except Exception as e:
        logger.error(f"Не удалось перезапустить с правами администратора: {e}")
        QMessageBox.warning(None, "Ошибка", "Не удалось выполнить перезапуск.")

# --- Основная точка входа приложения ---

def main(app_paths: Dict[str, Path]) -> int:
    """
    Основная функция, которая собирает и запускает приложение.
    """
    sys.excepthook = handle_exception
    setup_logging(log_dir=app_paths["logs"])

    app = QApplication(sys.argv)
    
    # Защита от повторного запуска
    lock_key = f"{ORG_NAME}_{APP_NAME}_Instance_Lock"
    shared_memory = QSharedMemory(lock_key)
    if not shared_memory.create(1):
        logger.warning("Попытка запуска второй копии приложения. Выход.")
        QMessageBox.warning(None, "Приложение уже запущено", f"{APP_NAME} уже работает.")
        return 0
    # Освобождаем память при выходе, чтобы "замок" снялся
    app.aboutToQuit.connect(shared_memory.detach)
    
    # Загрузка стилей
    qss_path = app_paths.get("base", Path()) / "winspector" / "resources" / "styles" / "main.qss"
    if qss_path.exists():
        try:
            with open(qss_path, "r", encoding="utf-8") as f:
                app.setStyleSheet(f.read())
            logger.info("Таблица стилей успешно загружена и применена.")
        except Exception as e:
            logger.error(f"Не удалось загрузить таблицу стилей: {e}")
    else:
        logger.warning(f"Файл стилей не найден: {qss_path}")

    # Устанавливаем иконку приложения глобально из файла
    # --- ИЗМЕНЕНИЕ: Используем путь из app_paths ---
    icon_path = app_paths.get("assets") / "app.ico"
    if icon_path.exists():
        app_icon = QIcon(str(icon_path))
        app.setWindowIcon(app_icon)
    else:
        logger.warning(f"Файл иконки не найден по пути: {icon_path}")

    # Установка AppUserModelID
    try:
        myappid = f'{ORG_NAME}.{APP_NAME}.{APP_VERSION}'
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
        logger.info(f"Установлен AppUserModelID: {myappid}")
    except (AttributeError, TypeError) as e:
        logger.warning(f"Не удалось установить AppUserModelID: {e}")

    # Проверка прав администратора
    if not check_admin_rights():
        logger.warning("Приложение запущено без прав администратора. Попытка перезапуска...")
        relaunch_as_admin()
        return 0
    
    # Настройка qasync
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)
    app.aboutToQuit.connect(loop.stop)
    
    try:
        # Инициализация ядра
        logger.info("Инициализация ядра WinSpectorCore...")
        
        # --- ИЗМЕНЕНИЕ: Формируем правильный словарь конфигурации для ядра ---
        base_path = app_paths.get("base", Path())
        core_config = {
            'kb_path': base_path / "winspector" / "data" / "knowledge_base.yaml",
            'telemetry_domains_path': base_path / "winspector" / "data" / "telemetry_domains.txt",
            # Добавляем другие ключи, которые могут понадобиться ядру в будущем
            'app_config': {
                'ai_ping_timeout': 10,
                'ai_cache_ttl': 3600,
            }
        }
        
        core_instance = WinSpectorCore(config=core_config)
        
        # Создание GUI
        logger.info("Создание главного окна MainWindow...")
        window = MainWindow(core_instance=core_instance, app_paths=app_paths)
        window.show()
        
        logger.info("Запуск главного цикла событий приложения.")
        with loop:
            exit_code = loop.run_forever()
        
        logger.info("Завершение работы.")
        return exit_code

    except Exception as e:
        logger.critical(f"Критическая ошибка при инициализации приложения: {e}", exc_info=True)
        QMessageBox.critical(None, "Ошибка инициализации", f"Не удалось запустить {APP_NAME}.\n\nПричина: {e}")
        return 1