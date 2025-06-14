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
import logging
import asyncio
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Dict

# --- 1. Импорт зависимостей ---
# Этот блок выполняется одним из первых, поэтому важна детальная ошибка.
try:
    from PyQt6.QtWidgets import QApplication, QMessageBox
    from PyQt6.QtCore import QSharedMemory
    from dotenv import load_dotenv
    import qasync
except ImportError as e:
    # Эта ошибка критична и должна быть видна сразу, даже если GUI не запустится.
    print(f"КРИТИЧЕСКАЯ ОШИБКА: Не найдены основные зависимости. "
          f"Пожалуйста, установите их командой 'pip install -r requirements.txt'.\nОшибка: {e}", file=sys.stderr)
    sys.exit(1)

# Загружаем переменные окружения из .env файла в самом начале
load_dotenv()

# Импорты из нашего проекта
try:
    # Метаданные приложения
    from src.winspector import APP_NAME, ORG_NAME, APP_VERSION
    # Основные компоненты
    from src.winspector.core import WinSpectorCore
    from src.winspector.gui import MainWindow
    # Скомпилированные ресурсы (стили, иконки)
    from src.winspector.resources import assets_rc
except ImportError as e:
    print(f"КРИТИЧЕСКАЯ ОШИБКА: Не удалось импортировать модули проекта. "
          f"Убедитесь, что вы запускаете приложение из корневой папки проекта.\nОшибка: {e}", file=sys.stderr)
    sys.exit(1)

# --- 2. Глобальный логгер ---
logger = logging.getLogger(__name__)


# --- 3. Функции-помощники ---

def setup_logging(log_dir: Path) -> None:
    """Настраивает глобальное логирование в файл и консоль."""
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / 'winspector.log'
        
        # 2MB на файл, храним 5 последних файлов
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

def handle_exception(exc_type, exc_value, exc_traceback) -> None:
    """Глобальный обработчик необработанных исключений (crash handler)."""
    if issubclass(exc_type, KeyboardInterrupt):
        # Позволяем Ctrl+C корректно завершить приложение
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return
        
    logger.critical(
        "Перехвачено необработанное исключение:", 
        exc_info=(exc_type, exc_value, exc_traceback)
    )
    
    QMessageBox.critical(
        None, 
        "Критическая ошибка приложения",
        "Произошла непредвиденная ошибка, которая привела к сбою.\n\n"
        "Подробная информация была записана в лог-файл.\n"
        "Приложение будет закрыто."
    )
    sys.exit(1)

def check_admin_rights() -> bool:
    """Проверяет, запущено ли приложение с правами администратора."""
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except AttributeError:
        # На системах, отличных от Windows, или в тестовых окружениях
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


# --- 4. Основная точка входа приложения ---

def main(app_paths: Dict[str, Path]) -> int:
    """
    Основная функция, которая собирает и запускает приложение.
    
    Args:
        app_paths: Словарь с путями к ресурсам и логам, переданный из main.py.
    
    Returns:
        Код выхода приложения.
    """
    # Шаг 1: Настройка обработчика сбоев
    sys.excepthook = handle_exception
    
    # Шаг 2: Настройка логирования
    setup_logging(log_dir=app_paths["logs"])

    # Шаг 3: Создание QApplication
    app = QApplication(sys.argv)
    
    # Шаг 4: Проверка прав администратора
    if not check_admin_rights():
        logger.warning("Приложение запущено без прав администратора. Предлагаем перезапуск.")
        reply = QMessageBox.question(
            None,
            "Требуются права администратора",
            f"Для полной оптимизации {APP_NAME} необходимо запустить от имени администратора. Перезапустить сейчас?",
            buttons=QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            defaultButton=QMessageBox.StandardButton.Yes
        )
        if reply == QMessageBox.StandardButton.Yes:
            relaunch_as_admin()
        else:
            logger.info("Пользователь отказался от перезапуска.")
        return 0 # Выходим в любом случае

    # Шаг 5: Защита от повторного запуска
    lock_key = f"{ORG_NAME}_{APP_NAME}_Instance_Lock"
    shared_memory = QSharedMemory(lock_key)
    if not shared_memory.create(1):
        logger.warning("Попытка запуска второй копии приложения. Выход.")
        QMessageBox.warning(None, "Приложение уже запущено", f"{APP_NAME} уже работает.")
        return 0
    # Освобождаем память при выходе, чтобы "замок" снялся
    app.aboutToQuit.connect(shared_memory.detach)

    # Шаг 6: Настройка метаданных и асинхронного цикла
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(ORG_NAME)
    app.setApplicationVersion(APP_VERSION)
    
    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)
    
    try:
        # Шаг 7: Инициализация ядра
        logger.info("Инициализация ядра WinSpectorCore...")
        
        # Формируем конфигурацию для ядра, используя пути из лаунчера
        app_config = {
            "kb_path": app_paths["base"] / "winspector" / "data" / "knowledge_base.yaml",
            "telemetry_domains_path": app_paths["base"] / "winspector" / "data" / "telemetry_domains.txt",
        }
        core_instance = WinSpectorCore(config=app_config)
        
        # Шаг 8: Создание и запуск GUI
        logger.info("Создание главного окна MainWindow...")
        window = MainWindow(core_instance=core_instance)
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