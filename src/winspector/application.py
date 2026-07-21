# src/winspector/application.py
"""
Основной модуль приложения WinSpector Pro.
Содержит класс Application, который инкапсулирует всю логику запуска.
"""

import asyncio
import contextlib
import ctypes
import logging
import os
import subprocess
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path


# --- Аварийный MessageBox, не зависящий от PyQt ---
def emergency_message_box(title: str, message: str):
    """Показывает системное окно с сообщением. Используется при сбоях до инициализации QApplication."""
    ctypes.windll.user32.MessageBoxW(0, message, title, 0x10)  # MB_ICONERROR


try:
    import qasync
    from dotenv import load_dotenv
    from PyQt6.QtCore import QSharedMemory
    from PyQt6.QtGui import QIcon
    from PyQt6.QtWidgets import QApplication, QMessageBox
except ImportError as e:
    error_msg = (
        f"КРИТИЧЕСКАЯ ОШИБКА: Не найдены основные зависимости: {e}\n\n"
        f"Пожалуйста, установите их командой 'pip install -r requirements.txt'."
    )
    emergency_message_box("Ошибка зависимостей", error_msg)
    sys.exit(1)


def _load_environment() -> None:
    """
    Загружает .env.

    Искать только в рабочей директории недостаточно: у собранного .exe она
    зависит от того, откуда его запустили, поэтому файл рядом с исполняемым
    файлом — основной ожидаемый вариант.
    """
    candidates = [
        Path(sys.executable).parent / ".env",  # рядом с .exe
        Path.cwd() / ".env",
        Path(__file__).resolve().parents[2] / ".env",  # корень репозитория
    ]
    for candidate in candidates:
        if candidate.is_file():
            load_dotenv(candidate, override=False)
            logging.getLogger(__name__).debug("Загружен .env: %s", candidate)
    load_dotenv(override=False)


_load_environment()

# Импорты пакета намеренно идут после _load_environment(): модули ядра
# читают переменные окружения уже на этапе импорта.
from src.winspector import APP_ID, APP_NAME, APP_VERSION  # noqa: E402
from src.winspector.core import WinSpectorCore  # noqa: E402
from src.winspector.core.exceptions import WinSpectorError  # noqa: E402
from src.winspector.gui import MainWindow  # noqa: E402
from src.winspector.gui.api_key_dialog import ensure_api_key_configured  # noqa: E402

logger = logging.getLogger(__name__)

# Пределы ожидания при выходе. Их задача — не дать одному зависшему запросу
# держать процесс: окно уже закрыто, и пользователь ждёт освобождения машины.
CORE_SHUTDOWN_TIMEOUT = 10
TASK_CANCEL_TIMEOUT = 5


class Application:
    """
    Класс, инкапсулирующий жизненный цикл приложения WinSpector Pro.
    """

    def __init__(self, app_paths: dict[str, Path]):
        self.app_paths = app_paths
        self.q_app: QApplication | None = None
        self.shared_memory: QSharedMemory | None = None
        self.core_instance: WinSpectorCore | None = None
        self.main_window: MainWindow | None = None
        self.log_file_path: Path | None = None
        self.ai_enabled: bool = False

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

        # Ключ запрашивается до создания ядра и до любых действий с системой.
        # Отказ от ИИ — допустимый выбор, а не ошибка: приложение продолжит
        # работать по встроенной базе знаний.
        self.ai_enabled = ensure_api_key_configured()

        if not self._initialize_core():
            return False

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
            # `run_forever` не возвращает код возврата — он всегда None.
            loop.run_forever()

            # Завершение выполняется здесь, а не по сигналу `aboutToQuit`.
            # Задача, созданная в обработчике этого сигнала, не успевала
            # отработать: цикл событий к тому моменту уже останавливался,
            # и процессы пула WMI оставались висеть после закрытия окна.
            loop.run_until_complete(self._shutdown())

        return 0

    def _setup_logging(self):
        log_dir = self.app_paths["logs"]
        log_dir.mkdir(parents=True, exist_ok=True)
        self.log_file_path = log_dir / "winspector.log"

        # ### УЛУЧШЕНИЕ: Конфигурируемый уровень логирования ###
        log_level_str = os.getenv("LOG_LEVEL", "INFO").upper()
        log_level = getattr(logging, log_level_str, logging.INFO)

        file_handler = RotatingFileHandler(
            self.log_file_path, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s - %(levelname)s - [%(name)s:%(lineno)d] - %(message)s")
        )

        # Консоль русской Windows использует cp1251. Имя файла или службы с
        # символом вне этой кодировки иначе роняет само логирование
        # UnicodeEncodeError — то есть падает диагностика, а не только вывод.
        for stream in (sys.stdout, sys.stderr):
            reconfigure = getattr(stream, "reconfigure", None)
            if reconfigure is not None:
                with contextlib.suppress(OSError, ValueError):
                    reconfigure(encoding="utf-8", errors="replace")

        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))

        root_logger = logging.getLogger()
        root_logger.setLevel(log_level)
        if root_logger.hasHandlers():
            root_logger.handlers.clear()
        root_logger.addHandler(file_handler)
        root_logger.addHandler(console_handler)
        logger.info(
            f"Система логирования для {APP_NAME} v{APP_VERSION} инициализирована. Уровень: {log_level_str}"
        )

    def _setup_exception_hook(self):
        self.original_hook = sys.excepthook
        sys.excepthook = self._handle_exception

    def _handle_exception(self, exc_type, exc, tb):
        logger.critical("Перехвачено необработанное исключение:", exc_info=(exc_type, exc, tb))

        if self.q_app and not self.q_app.property("is_shutting_down"):
            QMessageBox.critical(
                None,
                "Критическая ошибка",
                f"Произошла непредвиденная ошибка: {exc}\n\nПодробности в файле winspector.log.",
            )
        else:
            emergency_message_box(
                "Критическая ошибка",
                f"Произошла непредвиденная ошибка: {exc}\n\nПодробности записаны в лог-файл.",
            )

        if self.q_app:
            self.q_app.quit()

    def _check_single_instance(self) -> bool:
        """Проверяет, не запущена ли уже другая копия приложения."""
        # Ключ строится от APP_ID, а не от отображаемых названий: смена
        # бренда или названия продукта не должна ломать защиту от
        # повторного запуска.
        lock_key = f"{APP_ID}.InstanceLock"
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
                with qss_path.open(encoding="utf-8") as f:
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
        """
        Задаёт AppUserModelID — под ним Windows группирует окна приложения
        и связывает их с закреплённым на панели задач значком.

        Идентификатор берётся из константы и не содержит версию: раньше он
        собирался как `ORG_NAME.APP_NAME.APP_VERSION`, и после каждого
        обновления система считала приложение новым — закреплённый значок
        переставал работать.
        """
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
            logger.info("Установлен AppUserModelID: %s", APP_ID)
        except (AttributeError, OSError) as e:
            logger.warning("Не удалось установить AppUserModelID: %s", e)

    def _initialize_core(self) -> bool:
        """Создаёт ядро. Возвращает False, если запуск невозможен."""
        logger.info("Инициализация ядра WinSpectorCore...")
        core_config = {
            "kb_path": self.app_paths.get("kb_path"),
            "force_offline": not self.ai_enabled,
            "app_config": {
                "ai_model": os.getenv("GEMINI_MODEL") or "gemini-2.5-flash",
                "ai_request_timeout": 120,
                "ai_cache_ttl": 3600,
            },
        }
        try:
            self.core_instance = WinSpectorCore(config=core_config)
            return True
        except WinSpectorError as e:
            logger.critical("Не удалось инициализировать ядро: %s", e, exc_info=True)
            QMessageBox.critical(
                None,
                "Ошибка запуска",
                f"Не удалось подготовить приложение к работе.\n\n{e}",
            )
            return False

    def _initialize_gui(self):
        logger.info("Создание главного окна MainWindow...")
        self.main_window = MainWindow(core_instance=self.core_instance, app_paths=self.app_paths)

    def _setup_async_loop(self) -> qasync.QEventLoop:
        if sys.platform == "win32":
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

        loop = qasync.QEventLoop(self.q_app)
        asyncio.set_event_loop(loop)
        return loop

    async def _shutdown(self):
        """
        Останавливает ядро: фоновые задачи и пул процессов.

        Каждый этап ограничен по времени. Зависший запрос к ИИ имеет таймаут
        в две минуты, и без ограничения здесь окно закрывалось бы, а процесс
        продолжал бы висеть всё это время.
        """
        logger.info("Начало процедуры завершения работы...")

        if self.core_instance:
            try:
                await asyncio.wait_for(self.core_instance.shutdown(), CORE_SHUTDOWN_TIMEOUT)
            except TimeoutError:
                logger.warning(
                    "Ядро не остановилось за %d с — продолжаем завершение.",
                    CORE_SHUTDOWN_TIMEOUT,
                )
            except Exception:
                logger.exception("Ошибка при остановке ядра.")

        await self._cancel_pending_tasks()
        logger.info("Завершение работы.")

    @staticmethod
    async def _cancel_pending_tasks() -> None:
        """
        Отменяет задачи, оставшиеся после закрытия окна.

        Без этого asyncio печатает «Task was destroyed but it is pending!»,
        а сама работа продолжает выполняться в фоне.
        """
        current = asyncio.current_task()
        pending = [task for task in asyncio.all_tasks() if task is not current and not task.done()]
        if not pending:
            return

        logger.info("Отмена %d незавершённых задач...", len(pending))
        for task in pending:
            task.cancel()

        done, still_running = await asyncio.wait(pending, timeout=TASK_CANCEL_TIMEOUT)
        if still_running:
            # Скорее всего задача ждёт поток с внешней командой: прервать её
            # нельзя, но и держать пользователя дольше незачем.
            logger.warning(
                "%d задач не остановились за %d с: %s",
                len(still_running),
                TASK_CANCEL_TIMEOUT,
                ", ".join(sorted(task.get_name() for task in still_running)),
            )
        else:
            logger.debug("Все задачи (%d) остановлены.", len(done))

    @staticmethod
    def _check_admin_rights() -> bool:
        try:
            return ctypes.windll.shell32.IsUserAnAdmin() != 0
        except (AttributeError, OSError):
            return False

    def _relaunch_as_admin(self):
        """
        Перезапускает приложение с правами администратора.

        В собранном .exe `sys.executable` — это уже само приложение, поэтому
        путь к скрипту передавать нельзя: иначе программа получала бы
        собственный путь как аргумент. Прежний вариант вдобавок передавал
        `--log-file`, который нигде не разбирается.
        """
        logger.info("Отправлен запрос на перезапуск с правами администратора.")

        is_frozen = getattr(sys, "frozen", False)
        arguments = sys.argv[1:] if is_frozen else sys.argv
        params = subprocess.list2cmdline(arguments) if arguments else ""

        try:
            result = ctypes.windll.shell32.ShellExecuteW(
                None, "runas", sys.executable, params, None, 1
            )
            # ShellExecuteW возвращает значение > 32 при успехе; 5 (ACCESS_DENIED)
            # означает, что пользователь отклонил запрос UAC.
            if result <= 32:
                logger.warning("Перезапуск с правами администратора не состоялся (код %s).", result)
        except OSError as e:
            logger.error("Не удалось перезапустить с правами администратора: %s", e)


# --- Точка входа ---
def main(app_paths: dict[str, Path]) -> int:
    """
    Создает и запускает экземпляр приложения.
    """
    app_instance = Application(app_paths)
    if app_instance.initialize():
        return app_instance.exec()
    return 0  # Возвращаем 0, если инициализация не удалась (например, при перезапуске)
