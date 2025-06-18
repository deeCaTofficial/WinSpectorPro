# src/winspector/application.py
"""
Основной модуль приложения WinSpector Pro.
Содержит класс Application, который инкапсулирует всю логику запуска.
"""
import sys
import os
import ctypes
import logging
import asyncio
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Dict, Optional

# --- Аварийный MessageBox, не зависящий от PyQt ---
def emergency_message_box(title: str, message: str):
    """Показывает системное окно с сообщением. Используется при сбоях до инициализации QApplication."""
    ctypes.windll.user32.MessageBoxW(0, message, title, 0x10) # MB_ICONERROR

try:
    from PyQt6.QtWidgets import QApplication, QMessageBox
    from PyQt6.QtCore import QSharedMemory, QTimer
    from PyQt6.QtGui import QIcon
    from dotenv import load_dotenv
    import qasync
except ImportError as e:
    error_msg = f"КРИТИЧЕСКАЯ ОШИБКА: Не найдены основные зависимости: {e}\n\n" \
                f"Пожалуйста, установите их командой 'pip install -r requirements.txt'."
    emergency_message_box("Ошибка зависимостей", error_msg)
    sys.exit(1)

load_dotenv()

from src.winspector import APP_NAME, ORG_NAME, APP_VERSION
from src.winspector.core import WinSpectorCore
from src.winspector.gui import MainWindow

logger = logging.getLogger(__name__)

class Application:
    """
    Класс, инкапсулирующий жизненный цикл приложения WinSpector Pro.
    """
    def __init__(self, app_paths: Dict[str, Path]):
        self.app_paths = app_paths
        self.q_app: Optional[QApplication] = None
        self.shared_memory: Optional[QSharedMemory] = None
        self.core_instance: Optional[WinSpectorCore] = None
        self.main_window: Optional[MainWindow] = None
        self.log_file_path: Optional[Path] = None

        self._setup_exception_hook()

    def initialize(self) -> bool:
        """Выполняет всю предварительную настройку приложения."""
        self._setup_logging()

        if not self._check_admin_rights():
            self._relaunch_as_admin()
            return False

        self.q_app = QApplication(sys.argv)
        
        if not self._check_single_instance():
            QMessageBox.warning(None, "Приложение уже запущено", f"{APP_NAME} уже работает.")
            return False
            
        self._apply_styles()
        self._set_app_icon()
        self._set_app_user_model_id()
        self._initialize_core()
        self._initialize_gui()
        
        return True

    def exec(self) -> int:
        """Запускает главный цикл событий приложения."""
        if not self.q_app or not self.main_window:
            logger.critical("Попытка запуска без предварительной инициализации.")
            return 1

        loop = self._setup_async_loop()
        
        logger.info("Запуск главного цикла событий приложения.")
        self.main_window.show()
        
        with loop:
            exit_code = loop.run_forever()
        
        return exit_code

    def _setup_logging(self):
        log_dir = self.app_paths["logs"]
        log_dir.mkdir(parents=True, exist_ok=True)
        self.log_file_path = log_dir / 'winspector.log'
        
        # ### УЛУЧШЕНИЕ: Конфигурируемый уровень логирования ###
        log_level_str = os.getenv("LOG_LEVEL", "INFO").upper()
        log_level = getattr(logging, log_level_str, logging.INFO)

        file_handler = RotatingFileHandler(self.log_file_path, maxBytes=5*1024*1024, backupCount=3, encoding='utf-8')
        file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - [%(name)s:%(lineno)d] - %(message)s'))
        
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(logging.Formatter('[%(levelname)s] %(message)s'))
        
        root_logger = logging.getLogger()
        root_logger.setLevel(log_level)
        if root_logger.hasHandlers():
            root_logger.handlers.clear()
        root_logger.addHandler(file_handler)
        root_logger.addHandler(console_handler)
        logger.info(f"Система логирования для {APP_NAME} v{APP_VERSION} инициализирована. Уровень: {log_level_str}")

    def _setup_exception_hook(self):
        self.original_hook = sys.excepthook
        sys.excepthook = self._handle_exception

    def _handle_exception(self, exc_type, exc, tb):
        logger.critical("Перехвачено необработанное исключение:", exc_info=(exc_type, exc, tb))
        
        if self.q_app and not self.q_app.property("is_shutting_down"):
            QMessageBox.critical(None, "Критическая ошибка", f"Произошла непредвиденная ошибка: {exc}\n\nПодробности в файле winspector.log.")
        else:
            emergency_message_box("Критическая ошибка", f"Произошла непредвиденная ошибка: {exc}\n\nПодробности записаны в лог-файл.")
        
        if self.q_app:
            self.q_app.quit()

    def _check_single_instance(self) -> bool:
        """Проверяет, не запущена ли уже другая копия приложения."""
        lock_key = f"{ORG_NAME}_{APP_NAME}_Instance_Lock"
        self.shared_memory = QSharedMemory(lock_key)
        if not self.shared_memory.create(1):
            logger.warning("Попытка запуска второй копии приложения. Выход.")
            return False
        
        # Освобождаем память при выходе, чтобы "замок" снялся
        self.q_app.aboutToQuit.connect(self.shared_memory.detach)
        return True

    def _apply_styles(self):
        qss_path = self.app_paths.get("base") / "winspector" / "resources" / "styles" / "main.qss"
        if qss_path.exists():
            try:
                with open(qss_path, "r", encoding="utf-8") as f:
                    self.q_app.setStyleSheet(f.read())
                logger.info("Таблица стилей успешно загружена и применена.")
            except Exception as e:
                logger.error(f"Не удалось загрузить таблицу стилей: {e}")
        else:
            logger.warning(f"Файл стилей не найден: {qss_path}")

    def _set_app_icon(self):
        icon_path = self.app_paths.get("assets") / "app.ico"
        if icon_path.exists():
            self.q_app.setWindowIcon(QIcon(str(icon_path)))
        else:
            logger.warning(f"Файл иконки не найден по пути: {icon_path}")

    def _set_app_user_model_id(self):
        """Устанавливает AppUserModelID для корректного отображения иконки в панели задач."""
        try:
            myappid = f'{ORG_NAME}.{APP_NAME}.{APP_VERSION}'
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
            logger.info(f"Установлен AppUserModelID: {myappid}")
        except (AttributeError, TypeError) as e:
            logger.warning(f"Не удалось установить AppUserModelID: {e}")

    def _initialize_core(self):
        logger.info("Инициализация ядра WinSpectorCore...")
        core_config = {
            'kb_path': self.app_paths.get('kb_path'),
            'app_config': {'ai_ping_timeout': 10, 'ai_cache_ttl': 3600}
        }
        self.core_instance = WinSpectorCore(config=core_config)

    def _initialize_gui(self):
        logger.info("Создание главного окна MainWindow...")
        self.main_window = MainWindow(core_instance=self.core_instance, app_paths=self.app_paths)

    def _setup_async_loop(self) -> qasync.QEventLoop:
        if sys.platform == "win32":
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        
        loop = qasync.QEventLoop(self.q_app)
        asyncio.set_event_loop(loop)
        self.q_app.aboutToQuit.connect(lambda: loop.create_task(self._shutdown()))
        return loop

    async def _shutdown(self):
        logger.info("Начало процедуры завершения работы...")
        if self.core_instance:
            await self.core_instance.shutdown()
        logger.info("Завершение работы.")
        QTimer.singleShot(50, self.q_app.quit)

    @staticmethod
    def _check_admin_rights() -> bool:
        try:
            return ctypes.windll.shell32.IsUserAnAdmin() != 0
        except (AttributeError, OSError):
            return False

    def _relaunch_as_admin(self):
        logger.info("Отправлен запрос на перезапуск с правами администратора.")
        try:
            # ### УЛУЧШЕНИЕ: Передаем путь к лог-файлу новому процессу ###
            params = f'"{sys.argv[0]}" --log-file "{self.log_file_path}"' if self.log_file_path else " ".join(sys.argv)
            ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, params, None, 1)
        except Exception as e:
            logger.error(f"Не удалось перезапустить с правами администратора: {e}")

# --- Точка входа ---
def main(app_paths: Dict[str, Path]) -> int:
    """
    Создает и запускает экземпляр приложения.
    """
    app_instance = Application(app_paths)
    if app_instance.initialize():
        return app_instance.exec()
    return 0 # Возвращаем 0, если инициализация не удалась (например, при перезапуске)