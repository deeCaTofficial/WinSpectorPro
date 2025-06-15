# src/winspector/gui/main_window.py
"""Главное окно приложения WinSpector Pro."""
import asyncio
import logging
from typing import Optional
import sys
from ..resources import assets_rc
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QPushButton, QMessageBox,
    QProgressBar, QLabel, QStackedWidget, QFrame, QTextBrowser,
    QGraphicsOpacityEffect, QStackedLayout, QApplication, QHBoxLayout,
    QSizePolicy
)
from PyQt6.QtCore import (
    Qt, QThread, pyqtSignal, QPropertyAnimation, QEasingCurve, QTimer,
    QSequentialAnimationGroup, QFile, QIODevice, QPoint, QEvent
)
from PyQt6.QtGui import QIcon, QMouseEvent
from .widgets.title_bar import TitleBar
from .widgets.animated_widgets import PulsingButton, AnimatedCounterLabel
from .widgets.neural_background import NeuralBackgroundWidget
from ..core.analyzer import WinSpectorCore

try:
    # Относительный импорт для констант приложения
    from .. import APP_NAME, APP_VERSION
except ImportError:
    # Резервный вариант для случаев, когда скрипт может запускаться в другом контексте
    APP_NAME = "WinSpector Pro"
    APP_VERSION = "1.0.0"

try:
    import pythoncom
except ImportError:
    pythoncom = None

logger = logging.getLogger(__name__)

class MainWindow(QMainWindow):
    """Основное окно приложения."""
    progress_updated = pyqtSignal(int, str)
    optimization_finished = pyqtSignal(str)
    optimization_error = pyqtSignal(Exception)
    def __init__(self, core_instance: WinSpectorCore, app_paths: dict):
        super().__init__()
        logger.info("MainWindow: Инициализация.")
        self.core = core_instance
        self.app_paths = app_paths
        self.optimization_task: Optional[asyncio.Task] = None
        self.is_optimizing = False
        self.is_manually_maximized = False
        self.previous_geometry = self.geometry()
        self._setup_window()
        self._setup_ui()
        self._connect_signals()
        self.go_to_home_page()
    def _setup_window(self):
        self.setWindowTitle(f"{APP_NAME} v{APP_VERSION}")
        icon_path = self.app_paths.get("assets") / "app.ico"
        if icon_path.exists(): self.setWindowIcon(QIcon(str(icon_path)))
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMinimumSize(850, 700)
        self.resize(850, 700)
        self.container = QWidget()
        self.setCentralWidget(self.container)
    def _setup_ui(self):
        root_layout = QStackedLayout(self.container)
        root_layout.setStackingMode(QStackedLayout.StackingMode.StackAll)
        self.foreground_container = QWidget()
        self.foreground_container.setObjectName("GlassContainer")
        root_layout.addWidget(self.foreground_container)
        self.background_widget = NeuralBackgroundWidget()
        root_layout.addWidget(self.background_widget)
        root_layout.setCurrentWidget(self.foreground_container)
        panel_layout = QVBoxLayout(self.foreground_container)
        panel_layout.setContentsMargins(0, 0, 0, 0)
        panel_layout.setSpacing(0)
        self.title_bar = TitleBar(self)
        panel_layout.addWidget(self.title_bar)
        content_area = QWidget()
        panel_layout.addWidget(content_area, 1) # Растягиваем на все доступное место
        content_layout = QVBoxLayout(content_area)
        content_layout.setContentsMargins(10, 10, 10, 10)
        content_layout.setSpacing(10)
        self.stacked_widget = QStackedWidget()
        content_layout.addWidget(self.stacked_widget, 1)
        self.home_page = self._create_home_page()
        self.processing_page = self._create_processing_page()
        self.results_page = self._create_results_page()
        self.stacked_widget.addWidget(self.home_page)
        self.stacked_widget.addWidget(self.processing_page)
        self.stacked_widget.addWidget(self.results_page)
    def _create_home_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("HomePageWrapper")
        layout = QVBoxLayout(page)
        rocket_icon_path = self.app_paths.get("assets") / "rocket.png"
        rocket_icon = QIcon(str(rocket_icon_path)) if rocket_icon_path.exists() else QIcon()
        self.pulsing_button = PulsingButton(rocket_icon, text="")
        self.pulsing_button.setFixedSize(220, 220)

        layout.addStretch()
        layout.addWidget(self.pulsing_button, 0, Qt.AlignmentFlag.AlignCenter)
        layout.addStretch()
        return page
    def _create_processing_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("ProcessingPage")
        layout = QVBoxLayout(page)
        layout.setSpacing(15)
        self.status_label = QLabel()
        self.status_label.setObjectName("StatusLabel")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedWidth(400)
        self.cancel_button = QPushButton("Отмена")
        self.cancel_button.setObjectName("CancelButton")
        layout.addStretch()
        layout.addWidget(self.status_label, 0, Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.progress_bar, 0, Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.cancel_button, 0, Qt.AlignmentFlag.AlignCenter)
        layout.addStretch()
        return page
    def _create_results_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("ResultsPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 0, 20, 20)
        result_title = QLabel("Отчет об оптимизации")
        result_title.setObjectName("ResultTitle")
        self.report_browser = QTextBrowser()
        self.report_browser.setObjectName("ReportBrowser")
        self.report_browser.setOpenExternalLinks(True)
        self.back_button = QPushButton("Превосходно!")
        layout.addWidget(result_title, 0, Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.report_browser, 1)
        layout.addWidget(self.back_button, 0, Qt.AlignmentFlag.AlignCenter)
        return page
    def showEvent(self, event):
        super().showEvent(event)
        self.background_widget.start_animation()
    def hideEvent(self, event):
        super().hideEvent(event)
        self.background_widget.stop_animation()
    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == event.Type.WindowStateChange:
            self.is_manually_maximized = bool(self.windowState() & Qt.WindowState.WindowMaximized)
            self._update_foreground_style()
    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.title_bar.setFixedWidth(self.width())
        self._update_foreground_style()
    def _update_foreground_style(self):
        """Обновляет стиль переднего контейнера в зависимости от состояния окна."""
        if hasattr(self, 'foreground_container'):
            is_max = self.isMaximized() or self.is_manually_maximized
            if self.foreground_container.property("maximized") != is_max:
                self.foreground_container.setProperty("maximized", is_max)
                self.foreground_container.style().unpolish(self.foreground_container)
                self.foreground_container.style().polish(self.foreground_container)
                self.foreground_container.update()
    def _connect_signals(self):
        self.pulsing_button.clicked.connect(self.start_autonomous_optimization)
        self.cancel_button.clicked.connect(self.cancel_optimization)
        self.back_button.clicked.connect(self.go_to_home_page)
    def start_autonomous_optimization(self):
        """Запускает асинхронную задачу оптимизации в основном event loop."""
        if self.is_optimizing:
            logger.warning("Попытка запустить оптимизацию, когда она уже запущена.")
            return
        logger.info("Запрос на запуск оптимизации.")
        self.is_optimizing = True
        self.pulsing_button.pause()
        self.stacked_widget.setCurrentWidget(self.processing_page)
        self.status_label.setText("Подготовка к анализу...")
        self.progress_bar.setValue(0)
        self.cancel_button.setEnabled(True)
        async def optimization_wrapper():
            """Асинхронная обертка для обработки результатов и ошибок."""
            try:
                def is_cancelled():
                    task = asyncio.current_task()
                    return task.cancelled() if task else False
                
                report = await self.core.run_autonomous_optimization(
                    is_cancelled=is_cancelled,
                    progress_callback=self._update_progress
                )
                self._on_optimization_finished(report)
            except asyncio.CancelledError:
                logger.info("Оптимизация была успешно отменена.")
                self._on_optimization_cancelled()
            except Exception as e:
                logger.error(f"Произошла ошибка во время оптимизации: {e}", exc_info=True)
                self._on_optimization_error(e)
        self.optimization_task = asyncio.create_task(optimization_wrapper())
    def cancel_optimization(self):
        """Отменяет выполняющуюся асинхронную задачу."""
        logger.info("Запрос на отмену оптимизации.")
        if self.optimization_task and not self.optimization_task.done():
            self.optimization_task.cancel()
            self.cancel_button.setEnabled(False)
            self.status_label.setText("Отмена операции...")
    def go_to_home_page(self):
        self.stacked_widget.setCurrentWidget(self.home_page)
        self.pulsing_button.resume()
    def _update_progress(self, value: int, text: str):
        """Слот для обновления виджетов прогресса."""
        logger.debug(f"MainWindow: Получен сигнал progress_updated: value={value}, text='{text}'")
        self.progress_bar.setValue(value)
        self.status_label.setText(text)
    def _on_optimization_finished(self, final_report: str):
        logger.info("Обработка успешного завершения оптимизации.")
        self.is_optimizing = False
        self.optimization_task = None
        self.progress_bar.setValue(100)
        self.report_browser.setMarkdown(str(final_report))
        self.stacked_widget.setCurrentWidget(self.results_page)
    def _on_optimization_error(self, error: Exception):
        logger.error(f"Обработка ошибки оптимизации: {error}", exc_info=True)
        self.is_optimizing = False
        self.optimization_task = None
        QMessageBox.critical(self, "Ошибка", f"Произошла критическая ошибка: {error}")
        self.go_to_home_page()
    def _on_optimization_cancelled(self):
        """Обрабатывает завершение отмененной оптимизации."""
        logger.info("Обработка отмененной оптимизации.")
        self.is_optimizing = False
        self.optimization_task = None
        self.go_to_home_page()
    def closeEvent(self, event):
        logger.info("MainWindow: Получен сигнал closeEvent.")
        app = QApplication.instance()
        if app: app.setProperty("is_shutting_down", True)
        event.accept()