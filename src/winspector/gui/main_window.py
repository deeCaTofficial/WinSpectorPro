# src/winspector/gui/main_window.py
"""
Главное окно приложения WinSpector Pro.

Отвечает за отображение страниц, управление потоком оптимизации
и взаимодействие с пользователем.
"""
import asyncio
from typing import Optional

# Импортируем скомпилированные ресурсы из их правильного расположения
from src.winspector.resources import assets_rc

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QPushButton, QMessageBox,
    QProgressBar, QLabel, QStackedWidget, QFrame, QTextBrowser,
    QGraphicsOpacityEffect
)
from PyQt6.QtCore import (
    Qt, QThread, pyqtSignal, QPropertyAnimation, QEasingCurve, QTimer,
    QSequentialAnimationGroup, QFile, QIODevice
)

# Импортируем компоненты из нашего проекта
from src.winspector import APP_NAME, APP_VERSION
from src.winspector.core.analyzer import WinSpectorCore
from .icon_generator import IconGenerator
from .widgets.animated_widgets import PulsingButton

# Импорт pythoncom для корректной работы с WMI в потоках
try:
    import pythoncom
except ImportError:
    # На случай, если pywin32 не установлен (например, в CI/CD для Linux)
    pythoncom = None


class AsyncWorker(QThread):
    finished = pyqtSignal(object)
    error = pyqtSignal(Exception)

    # --- ИЗМЕНЕНИЕ 1: Добавляем флаг в конструктор ---
    def __init__(self, coro, *args, cancellable=False, **kwargs):
        super().__init__()
        self.coro = coro
        self.args = args
        self.cancellable = cancellable # <-- Новый флаг
        self.kwargs = kwargs
        self.is_cancelled = False

    def run(self):
        """Основная логика выполнения потока."""
        if pythoncom:
            pythoncom.CoInitialize()
        
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            # --- ИЗМЕНЕНИЕ 2: Формируем аргументы в зависимости от флага ---
            final_kwargs = self.kwargs.copy()
            if self.cancellable:
                final_kwargs['is_cancelled'] = lambda: self.is_cancelled

            result = loop.run_until_complete(
                self.coro(*self.args, **final_kwargs)
            )
            
            if not self.is_cancelled:
                self.finished.emit(result)
                
        except Exception as e:
            if not self.is_cancelled:
                self.error.emit(e)
            
        finally:
            loop.close()
            if pythoncom:
                pythoncom.CoUninitialize()

    def cancel(self):
        """Устанавливает флаг отмены для асинхронной задачи."""
        if self.cancellable: # <-- Отменять можно только отменяемые задачи
            self.is_cancelled = True
        else:
            import logging
            logging.warning("Попытка отменить задачу, которая не поддерживает отмену.")

class MainWindow(QMainWindow):
    """Главное окно приложения."""
    
    # Сигналы для безопасного взаимодействия между потоками
    progress_updated = pyqtSignal(int, str)
    optimization_finished = pyqtSignal(str)
    optimization_error = pyqtSignal(Exception)

    def __init__(self, core_instance: WinSpectorCore):
        super().__init__()
        self.core = core_instance
        self.worker: Optional[AsyncWorker] = None
        self.animation_group: Optional[QSequentialAnimationGroup] = None
        self.is_shutting_down = False

        self._setup_window()
        self._setup_ui()
        self._connect_signals()
        
        # Применяем эффекты прозрачности к страницам для анимации
        self._apply_opacity_effects()
        
        # Запускаем приложение с главной страницы
        self.go_to_home_page()

    def _setup_window(self):
        """Настраивает основные параметры окна."""
        self.setWindowTitle(f"{APP_NAME} v{APP_VERSION}")
        self.setWindowIcon(IconGenerator.get_icon("optimize", 64))
        self.setMinimumSize(850, 700)
        self.resize(850, 700)
        
        # Настройки для "безрамочного" и полупрозрачного окна
        # self.setWindowFlags(Qt.WindowType.FramelessWindowHint) # Можно включить для красоты
        # self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.oldPos = self.pos()

    def _setup_ui(self):
        """Создает и компонует все виджеты интерфейса."""
        self.glass_container = QFrame()
        self.glass_container.setObjectName("GlassContainer")
        self.setCentralWidget(self.glass_container)
        
        main_layout = QVBoxLayout(self.glass_container)
        main_layout.setContentsMargins(40, 20, 40, 20)
        main_layout.setSpacing(10)
        
        style_file = QFile(":/styles/main.qss")
        if style_file.open(QIODevice.OpenModeFlag.ReadOnly | QIODevice.OpenModeFlag.Text):
            self.setStyleSheet(style_file.readAll().data().decode("utf-8"))
        else:
            import logging
            logging.warning("Не удалось загрузить файл стилей 'main.qss' из ресурсов.")

        title = QLabel(APP_NAME)
        title.setObjectName("TitleLabel")
        subtitle = QLabel("Интеллектуальная дистилляция вашей системы")
        subtitle.setObjectName("SubtitleLabel")
        main_layout.addWidget(title, 0, Qt.AlignmentFlag.AlignCenter)
        main_layout.addWidget(subtitle, 0, Qt.AlignmentFlag.AlignCenter)
        
        self.stacked_widget = QStackedWidget()
        main_layout.addWidget(self.stacked_widget, 1)

        self.home_page = self._create_home_page()
        self.processing_page = self._create_processing_page()
        self.results_page = self._create_results_page()
        
        self.stacked_widget.addWidget(self.home_page)
        self.stacked_widget.addWidget(self.processing_page)
        self.stacked_widget.addWidget(self.results_page)

    def _create_home_page(self) -> QWidget:
        """Создает виджет главной страницы."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0,0,0,0)
        
        button_icon = IconGenerator.get_icon("optimize", 128)
        self.pulsing_button = PulsingButton(button_icon, "Начать дистилляцию")
        
        layout.addStretch(1)
        layout.addWidget(self.pulsing_button, 0, Qt.AlignmentFlag.AlignCenter)
        layout.addStretch(1)
        
        return page

    def _create_processing_page(self) -> QWidget:
        """Создает виджет страницы обработки."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0,0,0,0)
        
        self.status_label = QLabel("Запуск...")
        self.status_label.setObjectName("StatusLabel")
        
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setTextVisible(False)
        
        self.cancel_button = QPushButton("Отмена")
        
        layout.addStretch()
        layout.addWidget(self.status_label, 0, Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.progress_bar)
        layout.addWidget(self.cancel_button, 0, Qt.AlignmentFlag.AlignCenter)
        layout.addStretch()
        
        return page

    def _create_results_page(self) -> QWidget:
        """Создает виджет страницы результатов."""
        page = QWidget()
        layout = QVBoxLayout(page)
        
        title = QLabel("Отчет об оптимизации")
        title.setObjectName("ResultTitle")
        
        self.report_browser = QTextBrowser()
        self.report_browser.setObjectName("ReportBrowser")
        self.report_browser.setOpenExternalLinks(True)
        
        self.back_button = QPushButton("Превосходно!")
        
        layout.addWidget(title, 0, Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.report_browser, 1)
        layout.addWidget(self.back_button, 0, Qt.AlignmentFlag.AlignCenter)
        
        return page

    def _apply_opacity_effects(self):
        """Применяет QGraphicsOpacityEffect ко всем страницам для анимации."""
        for page in [self.home_page, self.processing_page, self.results_page]:
            effect = QGraphicsOpacityEffect(page)
            page.setGraphicsEffect(effect)
            # Начальная прозрачность для неактивных страниц
            if page != self.home_page:
                effect.setOpacity(0.0)

    def _connect_signals(self):
        """Подключает все сигналы к слотам."""
        self.pulsing_button.clicked.connect(self.start_autonomous_optimization)
        self.cancel_button.clicked.connect(self.cancel_optimization)
        self.back_button.clicked.connect(self.go_to_home_page)
        
        self.progress_updated.connect(self._update_progress)
        self.optimization_finished.connect(self._on_optimization_finished)
        self.optimization_error.connect(self._on_optimization_error)

    # --- Слоты и логика управления ---

    def start_autonomous_optimization(self):
        """Запускает асинхронный процесс оптимизации."""
        if self.worker and self.worker.isRunning():
            return
            
        self.pulsing_button.setEnabled(False)
        self._switch_to_page(self.processing_page)
        self.cancel_button.setEnabled(True)
        
        self.worker = AsyncWorker(
            self.core.run_autonomous_optimization,
            progress_callback=self.progress_updated.emit,
            cancellable=True # <-- Указываем флаг
        )
        self.worker.finished.connect(self.optimization_finished.emit)
        self.worker.error.connect(self.optimization_error.emit)
        self.worker.start()

    def cancel_optimization(self):
        """Обрабатывает отмену операции пользователем."""
        if self.worker and self.worker.isRunning():
            self.status_label.setText("Отмена операции...")
            self.cancel_button.setEnabled(False)
            self.worker.cancel()
        
        QTimer.singleShot(500, self.go_to_home_page)

    def go_to_home_page(self):
        """Переключает интерфейс на главную страницу."""
        self._switch_to_page(self.home_page)
        self.pulsing_button.setEnabled(True)
        self.progress_bar.setValue(0)
        self.status_label.setText("Готов к работе")

    def _update_progress(self, value: int, text: str):
        """Слот для обновления UI в процессе оптимизации."""
        self.progress_bar.setValue(value)
        self.status_label.setText(text)

    def _on_optimization_finished(self, final_report: str):
        """Слот, вызываемый по успешному завершению оптимизации."""
        self._update_progress(100, "Готово!")
        self.report_browser.setMarkdown(final_report)
        self._switch_to_page(self.results_page)
        self.worker = None

    def _on_optimization_error(self, error: Exception):
        """Слот, вызываемый при ошибке в процессе оптимизации."""
        import traceback
        self.go_to_home_page()
        QMessageBox.critical(
            self, 
            "Критическая ошибка", 
            f"В процессе оптимизации произошла ошибка:\n\n{error}\n\n"
            f"Traceback:\n{traceback.format_exc()}"
        )
        self.worker = None

    def _switch_to_page(self, page: QWidget):
        """Переключает страницы с эффектом затухания."""
        current_widget = self.stacked_widget.currentWidget()
        if current_widget == page:
            return

        if self.animation_group and self.animation_group.state() == QPropertyAnimation.State.Running:
            self.animation_group.stop()

        fade_out_effect = current_widget.graphicsEffect()
        fade_in_effect = page.graphicsEffect()
        
        self.stacked_widget.setCurrentWidget(page)
        fade_in_effect.setOpacity(0.0)

        fade_out = QPropertyAnimation(fade_out_effect, b"opacity", self) # Указываем родителя
        fade_out.setDuration(150)
        fade_out.setStartValue(1.0)
        fade_out.setEndValue(0.0)
        fade_out.setEasingCurve(QEasingCurve.Type.InQuad)

        fade_in = QPropertyAnimation(fade_in_effect, b"opacity", self) # Указываем родителя
        fade_in.setDuration(200)
        fade_in.setStartValue(0.0)
        fade_in.setEndValue(1.0)
        fade_in.setEasingCurve(QEasingCurve.Type.OutQuad)

        # Теперь self.animation_group - это атрибут класса, он не будет удален сборщиком мусора
        self.animation_group = QSequentialAnimationGroup(self)
        self.animation_group.addAnimation(fade_out)
        self.animation_group.addAnimation(fade_in)
        
        # Очищаем ссылку на группу после завершения анимации
        self.animation_group.finished.connect(lambda: setattr(self, 'animation_group', None))
        
        self.animation_group.start()

    # --- Обработка перемещения безрамочного окна ---
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.oldPos = event.globalPosition().toPoint()

    def mouseMoveEvent(self, event):
        if hasattr(self, 'oldPos'):
            delta = event.globalPosition().toPoint() - self.oldPos
            self.move(self.x() + delta.x(), self.y() + delta.y())
            self.oldPos = event.globalPosition().toPoint()

    def closeEvent(self, event):
        """
        Перехватывает событие закрытия окна для грациозного завершения.
        """
        if self.is_shutting_down:
            # Если мы уже в процессе завершения, разрешаем окну закрыться.
            event.accept()
            return

        # Проверяем, есть ли незавершенные фоновые задачи в ядре.
        if self.core.background_tasks:
            self.is_shutting_down = True
            event.ignore()  # Отменяем стандартное закрытие окна.

            # Информируем пользователя
            self.setEnabled(False)
            QMessageBox.information(
                self, "Завершение",
                "Пожалуйста, подождите, завершаются фоновые операции...\n"
                "Окно закроется автоматически."
            )
            self.setWindowTitle(f"{self.windowTitle()} - Завершение...")

            # --- ФИНАЛЬНОЕ ИСПРАВЛЕНИЕ ---
            # Мы не будем вызывать корутину shutdown. Вместо этого мы будем
            # асинхронно проверять, опустел ли сет с фоновыми задачами.
            async def wait_for_tasks_and_close():
                # Ждем, пока сет background_tasks не станет пустым.
                # Метод .discard() в WinSpectorCore будет удалять оттуда
                # завершенные задачи.
                while self.core.background_tasks:
                    # Периодически проверяем, не блокируя GUI
                    await asyncio.sleep(0.2)
                
                # Как только все задачи завершились, вызываем self.close()
                # для фактического закрытия окна.
                self.close()

            try:
                # Запускаем нашу "ожидающую" корутину в главном цикле qasync
                loop = asyncio.get_running_loop()
                loop.create_task(wait_for_tasks_and_close())
            except RuntimeError:
                # На случай, если цикл уже как-то остановлен.
                self.close()
        else:
            # Если фоновых задач нет, просто разрешаем закрытие.
            event.accept()