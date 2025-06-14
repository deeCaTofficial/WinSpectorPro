# tests/gui/test_main_window.py
"""
Тесты для главного окна приложения MainWindow.

Эти тесты проверяют реакцию GUI на действия пользователя и асинхронные
события от ядра приложения.
"""
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

# Убедитесь, что QApplication создается до импорта виджетов
from PyQt6.QtWidgets import QApplication, QMessageBox
app = QApplication.instance() or QApplication([])

from src.winspector.gui.main_window import MainWindow
from src.winspector.core.analyzer import WinSpectorCore

# --- Фикстуры для подготовки тестового окружения ---

@pytest.fixture
def mock_core(mocker) -> MagicMock:
    """Фикстура, которая создает мок для ядра WinSpectorCore."""
    core = MagicMock(spec=WinSpectorCore)
    # Мокаем асинхронный метод, чтобы он был 'awaitable'
    core.run_autonomous_optimization = AsyncMock(return_value="Отчет об успешной оптимизации.")
    return core


@pytest.fixture
def window(qtbot, mock_core) -> MainWindow:
    """Фикстура, которая создает и показывает экземпляр MainWindow для тестов."""
    # Создаем окно, передавая в него наш мок ядра
    main_window = MainWindow(core_instance=mock_core)
    # Регистрируем виджет в qtbot для взаимодействия и автоматической очистки
    qtbot.addWidget(main_window)
    main_window.show()
    # Ждем, пока все события в очереди обработаются (например, показ окна)
    qtbot.waitExposed(main_window)
    return main_window


# --- Тесты для различных сценариев GUI ---

class TestMainWindowInitialization:
    """Группа тестов для проверки начального состояния окна."""

    def test_window_has_correct_title_and_initial_state(self, window: MainWindow):
        """Проверяет, что окно создается с правильным заголовком и на главной странице."""
        assert "WinSpector Pro" in window.windowTitle()
        assert window.stacked_widget.currentWidget() == window.home_page
        assert window.pulsing_button.isEnabled()


class TestOptimizationFlow:
    """Группа тестов для основного сценария оптимизации."""

    # Патчим AsyncWorker, чтобы он не запускал реальный поток
    @patch('src.winspector.gui.main_window.AsyncWorker')
    def test_clicking_start_button_initiates_optimization(self, mock_async_worker, window: MainWindow, qtbot, mock_core):
        """Проверяет, что клик по кнопке "Начать" запускает процесс и меняет UI."""
        # GIVEN (окно в начальном состоянии)
        
        # WHEN (имитируем клик)
        qtbot.mouseClick(window.pulsing_button, qtbot.qt_api.QtCore.Qt.MouseButton.LeftButton)

        # THEN
        # 1. AsyncWorker был создан с правильными параметрами
        mock_async_worker.assert_called_once_with(
            mock_core.run_autonomous_optimization,
            progress_callback=window.progress_updated.emit
        )
        
        # 2. Поток AsyncWorker был запущен
        mock_async_worker.return_value.start.assert_called_once()
        
        # 3. Интерфейс переключился на страницу обработки
        qtbot.waitUntil(lambda: window.stacked_widget.currentWidget() == window.processing_page, timeout=1000)
        
        # 4. Кнопка "Начать" была заблокирована
        assert not window.pulsing_button.isEnabled()

    def test_progress_updated_signal_updates_gui(self, window: MainWindow, qtbot):
        """Проверяет, что сигнал progress_updated обновляет прогресс-бар и статус."""
        # GIVEN
        # Вручную переключаем на страницу обработки для теста
        window.stacked_widget.setCurrentWidget(window.processing_page)
        
        # WHEN (имитируем сигнал от worker-а)
        window.progress_updated.emit(50, "Анализ системы...")
        
        # THEN
        # Ждем, пока GUI обновится
        qtbot.waitUntil(lambda: window.progress_bar.value() == 50, timeout=500)
        assert window.status_label.text() == "Анализ системы..."

    def test_optimization_finished_signal_shows_results_page(self, window: MainWindow, qtbot):
        """Проверяет, что сигнал optimization_finished показывает страницу с отчетом."""
        # GIVEN
        report_text = "### Все готово!\n- Освобождено: 500 МБ"
        
        # WHEN (имитируем сигнал об успешном завершении)
        window.optimization_finished.emit(report_text)
        
        # THEN
        # 1. Интерфейс переключился на страницу результатов
        qtbot.waitUntil(lambda: window.stacked_widget.currentWidget() == window.results_page, timeout=1000)
        
        # 2. Отчет отображается в QTextBrowser
        assert "Все готово!" in window.report_browser.toMarkdown()
        assert "500 МБ" in window.report_browser.toMarkdown()

    def test_optimization_error_signal_shows_message_box(self, window: MainWindow, qtbot, mocker):
        """Проверяет, что сигнал optimization_error показывает диалоговое окно с ошибкой."""
        # GIVEN
        # Мокаем статический метод QMessageBox.critical
        mock_message_box = mocker.patch('PyQt6.QtWidgets.QMessageBox.critical')
        error_message = "Ошибка подключения к API"
        
        # WHEN (имитируем сигнал об ошибке)
        window.optimization_error.emit(Exception(error_message))
        
        # THEN
        # 1. Было вызвано критическое диалоговое окно
        mock_message_box.assert_called_once()
        # 2. Сообщение об ошибке было передано в диалоговое окно
        # (Проверяем только часть, т.к. там еще будет traceback)
        assert error_message in mock_message_box.call_args[0][2]
        # 3. Окно вернулось на главную страницу
        qtbot.waitUntil(lambda: window.stacked_widget.currentWidget() == window.home_page, timeout=1000)

    # Патчим AsyncWorker, чтобы он не запускал реальный поток
    @patch('src.winspector.gui.main_window.AsyncWorker')
    def test_cancel_button_cancels_worker(self, mock_async_worker, window: MainWindow, qtbot):
        """Проверяет, что кнопка "Отмена" вызывает метод cancel у worker-а."""
        # GIVEN
        # Запускаем оптимизацию, чтобы создать worker
        qtbot.mouseClick(window.pulsing_button, qtbot.qt_api.QtCore.Qt.MouseButton.LeftButton)
        
        # Получаем мок-экземпляр worker-а
        mock_worker_instance = mock_async_worker.return_value
        
        # WHEN (имитируем клик по кнопке отмены)
        qtbot.mouseClick(window.cancel_button, qtbot.qt_api.QtCore.Qt.MouseButton.LeftButton)

        # THEN
        # 1. Метод cancel у worker-а был вызван
        mock_worker_instance.cancel.assert_called_once()
        # 2. Окно вернулось на главную страницу
        qtbot.waitUntil(lambda: window.stacked_widget.currentWidget() == window.home_page, timeout=1000)