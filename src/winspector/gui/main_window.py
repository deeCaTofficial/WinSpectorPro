# src/winspector/gui/main_window.py
"""Главное окно приложения WinSpector Pro."""

import asyncio
import logging
from datetime import datetime

from PyQt6.QtCore import QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..core import credentials
from ..core.analyzer import WinSpectorCore
from ..core.exceptions import WinSpectorError
from ..core.report_data import ReportData
from ..core.update_checker import UpdateInfo, check_latest_release
from . import frameless, message_dialog
from .language import apply_qt_translation, get_language, localize
from .settings_dialog import SettingsDialog, reports_path
from .theme import (
    GLYPH_CHECK,
    GLYPH_COPY,
    GLYPH_SETTINGS,
    SUCCESS,
    glyph_icon,
    scaled_pixmap,
)
from .widgets.backdrop import DotGridBackground
from .widgets.icon_button import IconButton
from .widgets.progress import LinearProgress, StepIndicator
from .widgets.report_view import ReportView
from .widgets.title_bar import TitleBar

try:
    # Относительный импорт для констант приложения
    from .. import APP_NAME, APP_VERSION
except ImportError:
    # Резервный вариант для случаев, когда скрипт может запускаться в другом контексте
    APP_NAME = "WinSpector Pro"
    APP_VERSION = "1.0.0"

logger = logging.getLogger(__name__)


# Ширина колонки, в которой выстроено содержимое страниц: на развёрнутом
# окне строки отчёта иначе растягивались бы на весь экран.
_COLUMN_WIDTH = 460
_REPORT_WIDTH = 800
# Полоса прогресса и список этапов одной ширины: их левые края совпадают.
_STEPS_WIDTH = 300
_APP_MARK_SIZE = 64
# Сколько держится надпись «Скопировано» на кнопке копирования.
_COPIED_FEEDBACK_MS = 1600
# Зазор между значком и текстом кнопки.
_ICON_GAP = 8


class MainWindow(QMainWindow):
    """
    Основное окно приложения.

    Сигналов для передачи результата здесь нет: ядро выполняется в том же
    цикле событий через `qasync`, поэтому корутина обновляет виджеты напрямую.
    Сигналы были нужны прежней схеме с отдельным QThread.
    """

    update_status_changed = pyqtSignal(object, bool)

    def __init__(self, core_instance: WinSpectorCore, app_paths: dict):
        super().__init__()
        logger.info("MainWindow: Инициализация.")
        self.core = core_instance
        self.language = get_language()
        self.core.report_language = self.language
        self.app_paths = app_paths
        self.optimization_task: asyncio.Task | None = None
        self._report_markdown = ""
        self._report_data: ReportData | None = None
        self._settings_dialog: SettingsDialog | None = None
        self._update_info: UpdateInfo | None = None
        self._update_checking = False
        self._update_check_task: asyncio.Task | None = None
        self._auto_update_scheduled = False
        self._closing = False
        self.is_optimizing = False
        self._current_stage = 0
        self._cancel_requested = False
        # Окно закрыли во время оптимизации: закроемся, когда сценарий
        # действительно остановится.
        self._close_after_stop = False
        self._native_frame: frameless.NativeFrame | None = None
        self._setup_window()
        self._setup_ui()
        self._connect_signals()
        self.go_to_home_page()
        self._retranslate()

    def _t(self, russian: str, english: str) -> str:
        return localize(self.language, russian, english)

    def _set_language(self, language: str) -> None:
        self.language = language
        self.core.report_language = language
        self._retranslate()

    def _retranslate(self) -> None:
        apply_qt_translation(self.language)
        self.title_bar.retranslate(self.language)
        self.settings_button.setToolTip(self._t("Настройки", "Settings"))
        self.settings_button.setAccessibleName(self._t("Открыть настройки", "Open settings"))
        self.home_title.setText(self._t("Проверка и очистка Windows", "Inspect and clean Windows"))
        self.home_lead.setText(
            self._t(
                # Перенос строки задан явно: так две фразы не рвутся посередине.
                "Удаляет мусор и остатки программ, настраивает службы.\n"
                "Перед изменениями создаётся точка восстановления.",
                "Removes junk and program leftovers, tunes services.\n"
                "A restore point is created before any change.",
            )
        )
        self.start_button.setText(self._t("Оптимизировать", "Optimize"))
        self.processing_heading.setText(self._t("Оптимизация Windows", "Optimizing Windows"))
        self.cancel_button.setText(self._t("Отменить", "Cancel"))
        if self._report_markdown:
            self.report_view.show_report(self._report_data, self._report_markdown, self.language)
        # Ширина по самой длинной надписи: «Скопировано» не сдвигает кнопку.
        metrics = self.copy_button.fontMetrics()
        longest = max(
            metrics.horizontalAdvance(text)
            for text in (
                self._t("Скопировать отчёт", "Copy report"),
                self._t("Скопировано", "Copied"),
            )
        )
        self.copy_button.setMinimumWidth(longest + 16 + _ICON_GAP + 40)
        self._show_copy_state(copied=False)
        self.back_button.setText(self._t("Готово", "Done"))
        self._set_stage(self._current_stage)
        self._refresh_ai_status()
        self._refresh_update_notice()

    def _setup_window(self):
        # Заголовок свой (`TitleBar`): в нём шестерёнка настроек, и он не
        # отделяет окно от фона полосой другого цвета. Перетаскивание,
        # привязку к краям экрана и изменение размера по-прежнему делает
        # Windows — это настраивает `frameless.attach`.
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint)
        self.setWindowTitle(APP_NAME)
        icon_path = self.app_paths.get("assets") / "app.ico"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))
        self.setMinimumSize(720, 580)
        available = self.screen().availableGeometry()
        self.resize(min(920, available.width() - 48), min(700, available.height() - 48))

    def _setup_ui(self):
        # Страницы прозрачные, поэтому подвижный фон виден за всем содержимым.
        self.backdrop = DotGridBackground()
        self.setCentralWidget(self.backdrop)
        layout = QVBoxLayout(self.backdrop)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Заголовок прозрачный: сетка точек идёт под ним без шва.
        self.title_bar = TitleBar(self)
        self.update_notice = QPushButton()
        self.update_notice.setObjectName("UpdateNotice")
        self.update_notice.setCursor(Qt.CursorShape.PointingHandCursor)
        self.update_notice.clicked.connect(lambda: self.open_settings(0))
        self.update_notice.hide()
        self.title_bar.leading.addWidget(self.update_notice)
        self.settings_button = IconButton(GLYPH_SETTINGS, glyph_size=16)
        self.settings_button.clicked.connect(lambda: self.open_settings(0))
        self.title_bar.trailing.addWidget(self.settings_button)
        layout.addWidget(self.title_bar)
        self._native_frame = frameless.attach(self, self.title_bar)

        self.stacked_widget = QStackedWidget()
        content = QVBoxLayout()
        content.setContentsMargins(20, 4, 20, 20)
        content.addWidget(self.stacked_widget)
        layout.addLayout(content, 1)
        self.home_page = self._create_home_page()
        self.processing_page = self._create_processing_page()
        self.results_page = self._create_results_page()
        self.stacked_widget.addWidget(self.home_page)
        self.stacked_widget.addWidget(self.processing_page)
        self.stacked_widget.addWidget(self.results_page)

    @staticmethod
    def _centered_column(page: QWidget, width: int) -> QVBoxLayout:
        """Колонка заданной ширины по центру страницы."""
        outer = QHBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        column = QWidget()
        column.setMaximumWidth(width)
        outer.addStretch(1)
        outer.addWidget(column, 100)
        outer.addStretch(1)
        layout = QVBoxLayout(column)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        return layout

    def _create_home_page(self) -> QWidget:
        page = QWidget()
        layout = self._centered_column(page, _COLUMN_WIDTH)

        self.app_mark = QLabel()
        self.app_mark.setFixedSize(_APP_MARK_SIZE, _APP_MARK_SIZE)
        self.app_mark.setVisible(self._app_icon_path().exists())
        self._refresh_app_mark()

        self.home_title = QLabel()
        self.home_title.setObjectName("Heading")
        self.home_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.home_title.setWordWrap(True)

        self.home_lead = QLabel()
        self.home_lead.setObjectName("Lead")
        self.home_lead.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.home_lead.setWordWrap(True)

        self.start_button = QPushButton()
        self.start_button.setObjectName("PrimaryAction")
        self.start_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.start_button.setMinimumWidth(240)

        # Состояние ИИ видно сразу, а не выясняется в середине оптимизации.
        self.ai_status_dot = QLabel()
        self.ai_status_dot.setObjectName("StatusDot")
        self.ai_status_dot.setFixedSize(8, 8)
        self.ai_status_label = QLabel()
        self.ai_status_label.setObjectName("AiStatusLabel")
        self.ai_settings_button = QPushButton()
        self.ai_settings_button.setObjectName("LinkButton")
        self.ai_settings_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.ai_settings_button.clicked.connect(lambda: self.open_settings(1))
        mode_row = QHBoxLayout()
        mode_row.setSpacing(0)
        mode_row.addStretch()
        mode_row.addWidget(self.ai_status_dot, 0, Qt.AlignmentFlag.AlignVCenter)
        mode_row.addSpacing(10)
        mode_row.addWidget(self.ai_status_label)
        mode_row.addSpacing(8)
        mode_row.addWidget(self.ai_settings_button)
        mode_row.addStretch()

        layout.addStretch(5)
        layout.addWidget(self.app_mark, 0, Qt.AlignmentFlag.AlignCenter)
        layout.addSpacing(24)
        layout.addWidget(self.home_title)
        layout.addSpacing(10)
        layout.addWidget(self.home_lead)
        layout.addSpacing(32)
        layout.addWidget(self.start_button, 0, Qt.AlignmentFlag.AlignCenter)
        layout.addSpacing(18)
        layout.addLayout(mode_row)
        layout.addStretch(6)

        self._refresh_ai_status()
        return page

    def _app_icon_path(self):
        return self.app_paths.get("assets") / "app.ico"

    def _refresh_app_mark(self) -> None:
        """Логотип под масштаб текущего экрана — без мыла и рваных краёв."""
        path = self._app_icon_path()
        if path.exists():
            self.app_mark.setPixmap(scaled_pixmap(path, _APP_MARK_SIZE, self.devicePixelRatioF()))

    def _refresh_ai_status(self) -> None:
        """Показывает, работает приложение с ИИ или по базе знаний."""
        connected = credentials.has_api_key()
        if connected:
            text = self._t("Gemini подключён", "Gemini connected")
            self.ai_settings_button.setText(self._t("Настроить", "Configure"))
        else:
            text = self._t("Без ИИ · безопасный режим", "Without AI · safe mode")
            self.ai_settings_button.setText(self._t("Подключить Gemini", "Connect Gemini"))
        mode = "connected" if connected else "offline"
        self.ai_status_label.setText(text)
        for widget in (self.ai_status_label, self.ai_status_dot):
            widget.setProperty("mode", mode)
            if style := widget.style():
                style.unpolish(widget)
                style.polish(widget)

    def open_settings(self, tab: int = 0) -> None:
        dialog = SettingsDialog(
            self,
            initial_tab=tab,
            update_info=self._update_info,
            update_checking=self._update_checking,
        )
        self._settings_dialog = dialog
        dialog.credentials_changed.connect(self._sync_ai_mode)
        dialog.language_changed.connect(self._set_language)
        dialog.check_updates_requested.connect(self.check_for_updates)
        self.update_status_changed.connect(dialog.set_update_state)
        try:
            dialog.exec()
        finally:
            self._settings_dialog = None
            self._sync_ai_mode()
            dialog.deleteLater()

    def _sync_ai_mode(self) -> None:
        self._refresh_ai_status()
        self.core.force_offline = not credentials.has_api_key()

    def _create_processing_page(self) -> QWidget:
        page = QWidget()
        layout = self._centered_column(page, _COLUMN_WIDTH)
        self.processing_heading = QLabel()
        self.processing_heading.setObjectName("PageTitle")
        self.processing_heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label = QLabel()
        self.status_label.setObjectName("ProgressStatus")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setWordWrap(True)
        self.progress_bar = LinearProgress()
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setFixedWidth(_STEPS_WIDTH)

        steps = QWidget()
        steps.setFixedWidth(_STEPS_WIDTH)
        steps_layout = QVBoxLayout(steps)
        steps_layout.setContentsMargins(0, 0, 0, 0)
        steps_layout.setSpacing(14)
        self.stage_indicators: list[StepIndicator] = []
        self.stage_labels: list[QLabel] = []
        for _ in range(5):
            indicator = StepIndicator()
            label = QLabel()
            label.setObjectName("StageLabel")
            row = QHBoxLayout()
            row.setSpacing(14)
            row.addWidget(indicator)
            row.addWidget(label, 1)
            steps_layout.addLayout(row)
            self.stage_indicators.append(indicator)
            self.stage_labels.append(label)

        self.cancel_button = QPushButton()
        self.cancel_button.setObjectName("CancelButton")
        self.cancel_button.setMinimumWidth(140)

        layout.addStretch(5)
        layout.addWidget(self.processing_heading)
        layout.addSpacing(10)
        layout.addWidget(self.status_label)
        layout.addSpacing(24)
        layout.addWidget(self.progress_bar, 0, Qt.AlignmentFlag.AlignCenter)
        layout.addSpacing(40)
        layout.addWidget(steps, 0, Qt.AlignmentFlag.AlignCenter)
        layout.addSpacing(40)
        layout.addWidget(self.cancel_button, 0, Qt.AlignmentFlag.AlignCenter)
        layout.addStretch(6)
        # Страница сама принимает фокус: иначе при старте он падает на
        # «Отменить», и вокруг кнопки сразу видна рамка фокуса.
        page.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        return page

    def _create_results_page(self) -> QWidget:
        page = QWidget()
        layout = self._centered_column(page, _REPORT_WIDTH)
        layout.setContentsMargins(12, 6, 0, 0)
        self.report_view = ReportView()
        self.report_view.open_quarantine.connect(lambda: self.open_settings(2))
        self.report_view.open_gemini_settings.connect(lambda: self.open_settings(1))
        self.copy_button = QPushButton()
        self.copy_button.setIconSize(QSize(16 + _ICON_GAP, 16))
        self._copied_timer = QTimer(self)
        self._copied_timer.setSingleShot(True)
        self._copied_timer.setInterval(_COPIED_FEEDBACK_MS)
        self._copied_timer.timeout.connect(lambda: self._show_copy_state(copied=False))
        self.back_button = QPushButton()
        self.back_button.setObjectName("PrimaryButton")
        self.back_button.setMinimumWidth(120)
        layout.addWidget(self.report_view, 1)
        layout.addSpacing(16)
        buttons = QHBoxLayout()
        # Кнопки — по правому краю карточек, а не полосы прокрутки.
        buttons.setContentsMargins(0, 0, 14, 0)
        buttons.setSpacing(10)
        buttons.addStretch()
        buttons.addWidget(self.copy_button)
        buttons.addWidget(self.back_button)
        layout.addLayout(buttons)
        return page

    def showEvent(self, event):
        super().showEvent(event)
        # До показа масштаб экрана ещё не известен точно.
        self._refresh_app_mark()
        if not self._auto_update_scheduled:
            self._auto_update_scheduled = True
            QTimer.singleShot(900, self.check_for_updates)

    def nativeEvent(self, event_type, message):
        # Рамку, края и заголовок окна для Windows описывает `frameless`.
        if self._native_frame is not None:
            handled = self._native_frame.handle(message)
            if handled is not None:
                return handled
        # Не super(): в PyQt6 6.11 вызов базового nativeEvent роняет процесс,
        # а сам он ничего не обрабатывает.
        return False, 0

    def check_for_updates(self) -> None:
        """Запускает проверку один раз за запрос, не задерживая интерфейс."""
        if self._closing or (self._update_check_task and not self._update_check_task.done()):
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.debug("Проверка обновлений ожидает асинхронный цикл приложения.")
            return
        self._update_checking = True
        self.update_status_changed.emit(self._update_info, True)
        self._update_check_task = loop.create_task(self._check_for_updates_async())

    async def _check_for_updates_async(self) -> None:
        try:
            result = await check_latest_release(APP_VERSION)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Неожиданная ошибка проверки обновлений.")
            result = UpdateInfo(APP_VERSION, error="Не удалось проверить обновления на GitHub.")
        if not self._closing:
            self._update_info = result
            self._update_checking = False
            self.update_status_changed.emit(result, False)
            self._refresh_update_notice()

    def _refresh_update_notice(self) -> None:
        info = self._update_info
        available = info is not None and info.available and not self.is_optimizing
        self.update_notice.setVisible(available)
        if available:
            self.update_notice.setText(
                self._t(
                    f"Доступна версия {info.latest_version}  →",
                    f"Version {info.latest_version} is available  →",
                )
            )

    def _connect_signals(self):
        self.start_button.clicked.connect(self.start_autonomous_optimization)
        self.cancel_button.clicked.connect(self.cancel_optimization)
        self.back_button.clicked.connect(self.go_to_home_page)
        self.copy_button.clicked.connect(self._copy_report)

    def _copy_report(self) -> None:
        QApplication.clipboard().setText(self._report_markdown)
        self._show_copy_state(copied=True)
        self._copied_timer.start()

    def _show_copy_state(self, copied: bool) -> None:
        if copied:
            self.copy_button.setText(self._t("Скопировано", "Copied"))
            self.copy_button.setIcon(glyph_icon(GLYPH_CHECK, 16, SUCCESS, SUCCESS, gap=_ICON_GAP))
        else:
            self.copy_button.setText(self._t("Скопировать отчёт", "Copy report"))
            self.copy_button.setIcon(glyph_icon(GLYPH_COPY, 16, gap=_ICON_GAP))

    def _show_page(self, page: QWidget) -> None:
        self.stacked_widget.setCurrentWidget(page)
        # На время работы настройки недоступны: шестерёнка просто исчезает,
        # а не висит серой.
        self.settings_button.setVisible(page is not self.processing_page)
        # Волны расходятся от логотипа, во время работы — от полосы прогресса.
        # Отчёт читают: фон под ним замирает и не отвлекает.
        origins = {self.home_page: self.app_mark, self.processing_page: self.progress_bar}
        self.backdrop.set_origin(origins.get(page))
        self.backdrop.set_paused(page is self.results_page)

    def start_autonomous_optimization(self):
        """Запускает асинхронную задачу оптимизации в основном event loop."""
        if self.is_optimizing:
            logger.warning("Попытка запустить оптимизацию, когда она уже запущена.")
            return
        logger.info("Запрос на запуск оптимизации.")
        self.is_optimizing = True
        self._cancel_requested = False
        self._refresh_update_notice()
        self.start_button.setEnabled(False)
        self.settings_button.setEnabled(False)
        self.update_notice.setEnabled(False)
        self._show_page(self.processing_page)
        self.backdrop.set_active(True)
        self.status_label.setText(self._t("Подготовка к анализу…", "Preparing analysis…"))
        self._set_stage(0)
        self.cancel_button.setEnabled(True)
        self.processing_page.setFocus()

        async def optimization_wrapper():
            """Асинхронная обертка для обработки результатов и ошибок."""
            try:
                report = await self.core.run_autonomous_optimization(
                    # Флаг, а не `task.cancelled()`: у выполняющейся задачи
                    # этот метод всегда возвращает False, поэтому прежняя
                    # проверка никогда не срабатывала.
                    is_cancelled=lambda: self._cancel_requested,
                    progress_callback=self._update_progress,
                )
                await self._save_report(report)
                self._on_optimization_finished(report, getattr(self.core, "last_report", None))
            except asyncio.CancelledError:
                logger.info("Оптимизация была успешно отменена.")
                self._on_optimization_cancelled()
            except Exception as e:
                logger.error(f"Произошла ошибка во время оптимизации: {e}", exc_info=True)
                self._on_optimization_error(e)

        self.optimization_task = asyncio.create_task(optimization_wrapper())

    async def _save_report(self, report: str) -> None:
        """Сохраняет итог для действия «Открыть папку отчётов»."""
        path = reports_path() / f"report-{datetime.now():%Y%m%d-%H%M%S-%f}.md"

        def write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(report, encoding="utf-8")

        try:
            await asyncio.to_thread(write)
        except OSError:
            logger.exception("Не удалось сохранить отчёт в %s", path)

    def cancel_optimization(self):
        """
        Запрашивает отмену.

        Сначала выставляется флаг: ядро проверяет его между шагами и
        останавливается в предсказуемой точке. Жёсткий `task.cancel()`
        прервал бы выполнение посреди операции с файловой системой.
        """
        if not self.is_optimizing or self._cancel_requested:
            return
        logger.info("Запрос на отмену оптимизации.")
        self._cancel_requested = True
        self.cancel_button.setEnabled(False)
        self.status_label.setText(
            self._t(
                "Остановка после завершения текущей операции…",
                "Stopping after the current operation…",
            )
        )

    def go_to_home_page(self):
        self._show_page(self.home_page)
        self.start_button.setEnabled(True)
        self.settings_button.setEnabled(True)
        self.update_notice.setEnabled(True)
        self._refresh_update_notice()
        # Иначе фокус получает первая кнопка окна — шестерёнка настроек.
        self.start_button.setFocus()

    def _update_progress(self, value: int, text: str):
        """Слот для обновления виджетов прогресса."""
        logger.debug(f"MainWindow: Получен сигнал progress_updated: value={value}, text='{text}'")
        if self.language == "en":
            messages = (
                "Creating a restore point…",
                "Analyzing the system…",
                "Preparing the plan…",
                "Optimizing…",
                "Preparing the report…",
                "Done!",
            )
            self.status_label.setText(
                messages[5]
                if value >= 100
                else messages[4]
                if value >= 95
                else messages[3]
                if value >= 70
                else messages[2]
                if value >= 55
                else messages[1]
                if value >= 15
                else messages[0]
            )
        else:
            # Ядро пишет многоточие тремя точками; в окне — типографское «…».
            self.status_label.setText(text.replace("...", "…"))
        if value >= 95:
            self._set_stage(4)
        elif value >= 70:
            self._set_stage(3)
        elif value >= 55:
            self._set_stage(2)
        elif value >= 15:
            self._set_stage(1)
        else:
            self._set_stage(0)

    def _set_stage(self, current: int) -> None:
        self._current_stage = current
        stages = (
            self._t("Точка восстановления", "Restore point"),
            self._t("Анализ системы", "System analysis"),
            self._t("Формирование плана", "Preparing the plan"),
            self._t("Оптимизация", "Optimization"),
            self._t("Отчёт", "Report"),
        )
        for index, (indicator, label) in enumerate(
            zip(self.stage_indicators, self.stage_labels, strict=True)
        ):
            state = "done" if index < current else "active" if index == current else "pending"
            indicator.set_state(state)
            label.setText(stages[index])
            label.setProperty("stage", state)
            if style := label.style():
                style.unpolish(label)
                style.polish(label)

    def _reset_state(self):
        """Возвращает окно в исходное состояние после завершения сценария."""
        self.is_optimizing = False
        self._cancel_requested = False
        self.optimization_task = None
        self.backdrop.set_active(False)
        self.start_button.setEnabled(True)
        self.settings_button.setEnabled(True)
        self.update_notice.setEnabled(True)
        self._refresh_update_notice()

        # Пользователь просил закрыть окно, но мы дожидались остановки
        # сценария. Теперь закрываться безопасно.
        if self._close_after_stop:
            logger.info("Сценарий остановлен — закрываем окно по отложенному запросу.")
            self._close_after_stop = False
            QTimer.singleShot(0, self.close)

    def _on_optimization_finished(self, final_report: str, data: ReportData | None = None):
        logger.info("Обработка успешного завершения оптимизации.")
        closing = self._close_after_stop
        self._reset_state()
        if closing:
            return
        self._set_stage(5)
        self._report_markdown = str(final_report)
        self._report_data = data
        self.report_view.show_report(data, self._report_markdown, self.language)
        self._copied_timer.stop()
        self._show_copy_state(copied=False)
        self._show_page(self.results_page)
        # Отчёт сразу листается клавишами, и ни одна кнопка не в рамке фокуса.
        self.report_view.setFocus()

    def _on_optimization_error(self, error: Exception):
        logger.error(f"Обработка ошибки оптимизации: {error}", exc_info=True)
        self._reset_state()
        # Ожидаемые ошибки (нет точки восстановления, недоступен ИИ) уже
        # содержат объяснение для пользователя; всё остальное — баг, и о нём
        # честнее сказать прямо, отправив человека в лог.
        if isinstance(error, WinSpectorError):
            message_dialog.notify(
                self,
                self._t("Не удалось выполнить оптимизацию", "Optimization failed"),
                str(error),
                kind="warning",
                language=self.language,
            )
        else:
            message_dialog.notify(
                self,
                self._t("Непредвиденная ошибка", "Unexpected error"),
                self._t(
                    f"{error}\n\nПодробности записаны в winspector.log.",
                    f"{error}\n\nDetails were written to winspector.log.",
                ),
                kind="error",
                language=self.language,
            )
        self.go_to_home_page()

    def _on_optimization_cancelled(self):
        """Обрабатывает завершение отмененной оптимизации."""
        logger.info("Обработка отмененной оптимизации.")
        self._reset_state()
        self.go_to_home_page()

    def closeEvent(self, event):
        """
        Закрывает окно, но не бросает начатую оптимизацию.

        Раньше окно закрывалось сразу, а сценарий продолжал работать:
        приложение удаляло файлы и меняло службы уже после того, как
        пользователь его «закрыл», а процесс висел в фоне до конца операции.
        """
        logger.info("MainWindow: Получен сигнал closeEvent.")

        if self._settings_dialog is not None and self._settings_dialog._restore_in_progress():
            event.ignore()
            return

        if self.is_optimizing:
            if not self._confirm_stop_before_close():
                event.ignore()
                return

            # Останавливаемся между шагами: обрывать операцию посреди работы
            # с реестром или службой опаснее, чем подождать несколько секунд.
            self._close_after_stop = True
            self._cancel_requested = True
            self.cancel_button.setEnabled(False)
            self.status_label.setText(
                self._t(
                    "Остановка после завершения текущей операции…",
                    "Stopping after the current operation…",
                )
            )
            event.ignore()
            return

        app = QApplication.instance()
        self._closing = True
        if self._update_check_task is not None and not self._update_check_task.done():
            self._update_check_task.cancel()
        if app:
            app.setProperty("is_shutting_down", True)
        event.accept()

    def _confirm_stop_before_close(self) -> bool:
        """Спрашивает, прерывать ли идущую оптимизацию."""
        return message_dialog.confirm(
            self,
            self._t("Остановить оптимизацию?", "Stop optimization?"),
            self._t(
                "Уже сделанные изменения сохранятся. Откатить их можно через "
                "точку восстановления, созданную перед началом.",
                "Changes already made will stay. You can roll them back with the "
                "restore point created before the start.",
            ),
            accept=self._t("Остановить и закрыть", "Stop and close"),
            reject=self._t("Продолжить", "Keep going"),
            destructive=True,
            kind="warning",
            language=self.language,
        )
