# tests/test_gui.py
"""
Тесты главного окна.

Отдельное внимание отмене: прежняя проверка через `task.cancelled()` у
выполняющейся задачи всегда возвращала False и не работала.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

pytest.importorskip("PyQt6", reason="PyQt6 не установлен")
pytestmark = [pytest.mark.gui]

from PyQt6.QtGui import QCloseEvent  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from winspector.core.exceptions import RestorePointError  # noqa: E402
from winspector.gui.main_window import MainWindow  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


class FakeCore:
    """Ядро-дублёр: фиксирует переданные колбэки, ничего не делает с системой."""

    def __init__(self, report: str = "## Готово", error: Exception | None = None) -> None:
        self.report = report
        self.error = error
        self.is_cancelled = None
        self.progress_callback = None
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def run_autonomous_optimization(self, is_cancelled=None, progress_callback=None):
        self.is_cancelled = is_cancelled
        self.progress_callback = progress_callback
        self.started.set()
        await self.release.wait()
        if self.error:
            raise self.error
        if is_cancelled and is_cancelled():
            raise asyncio.CancelledError
        return self.report

    async def shutdown(self, **kwargs: Any) -> None:
        return None


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    # Фоновая анимация не нужна и мешает детерминизму тестов.
    monkeypatch.setattr(
        "winspector.gui.widgets.neural_background.NeuralBackgroundWidget.start_animation",
        lambda self: None,
    )
    core = FakeCore()
    win = MainWindow(core_instance=core, app_paths={"assets": tmp_path})
    win.core = core
    yield win

    # Сбрасываем состояние перед закрытием: иначе `closeEvent` покажет
    # настоящий модальный вопрос «Остановить оптимизацию?» и прогон
    # тестов повиснет на нём навсегда.
    win.is_optimizing = False
    win._close_after_stop = False
    win.close()


class TestWindowSetup:
    def test_starts_on_home_page(self, window):
        assert window.stacked_widget.currentWidget() is window.home_page
        assert window.is_optimizing is False

    def test_cancel_flag_starts_clear(self, window):
        assert window._cancel_requested is False


class TestCancellation:
    def test_cancel_sets_the_flag_the_core_reads(self, window):
        """Ядро получает функцию, отражающую нажатие кнопки «Отмена»."""
        window.is_optimizing = True
        window._cancel_requested = False

        probe = lambda: window._cancel_requested  # noqa: E731
        assert probe() is False

        window.cancel_optimization()

        assert window._cancel_requested is True
        assert probe() is True, "функция отмены должна видеть новое состояние"

    def test_cancel_is_ignored_when_idle(self, window):
        window.is_optimizing = False
        window.cancel_optimization()
        assert window._cancel_requested is False

    def test_repeated_cancel_is_harmless(self, window):
        window.is_optimizing = True
        window.cancel_optimization()
        window.cancel_optimization()
        assert window._cancel_requested is True

    def test_cancel_disables_the_button(self, window):
        window.is_optimizing = True
        window.cancel_button.setEnabled(True)
        window.cancel_optimization()
        assert window.cancel_button.isEnabled() is False


class TestStateTransitions:
    def test_finish_shows_the_report(self, window):
        window.is_optimizing = True
        window._on_optimization_finished("## Всё хорошо")

        assert window.stacked_widget.currentWidget() is window.results_page
        assert window.is_optimizing is False
        assert window._cancel_requested is False
        assert "Всё хорошо" in window.report_browser.toPlainText()

    def test_cancel_returns_home(self, window):
        window.is_optimizing = True
        window._cancel_requested = True
        window._on_optimization_cancelled()

        assert window.stacked_widget.currentWidget() is window.home_page
        assert window._cancel_requested is False

    def test_expected_error_is_shown_as_warning(self, window, monkeypatch):
        """Ожидаемая ошибка не должна выглядеть как падение программы."""
        shown: dict[str, Any] = {}
        monkeypatch.setattr(
            "winspector.gui.main_window.QMessageBox.warning",
            lambda *args, **kwargs: shown.update(kind="warning", text=args[2]),
        )
        monkeypatch.setattr(
            "winspector.gui.main_window.QMessageBox.critical",
            lambda *args, **kwargs: shown.update(kind="critical"),
        )

        window._on_optimization_error(RestorePointError("защита системы отключена"))

        assert shown["kind"] == "warning"
        assert "защита системы" in shown["text"]

    def test_unexpected_error_is_shown_as_critical(self, window, monkeypatch):
        shown: dict[str, Any] = {}
        monkeypatch.setattr(
            "winspector.gui.main_window.QMessageBox.warning",
            lambda *args, **kwargs: shown.update(kind="warning"),
        )
        monkeypatch.setattr(
            "winspector.gui.main_window.QMessageBox.critical",
            lambda *args, **kwargs: shown.update(kind="critical"),
        )

        window._on_optimization_error(ValueError("неожиданный сбой"))

        assert shown["kind"] == "critical"

    def test_error_resets_state(self, window, monkeypatch):
        monkeypatch.setattr("winspector.gui.main_window.QMessageBox.critical", lambda *a, **k: None)
        window.is_optimizing = True
        window._on_optimization_error(ValueError("сбой"))

        assert window.is_optimizing is False
        assert window.optimization_task is None

    def test_progress_updates_widgets(self, window):
        window._update_progress(42, "Идёт очистка")
        assert window.progress_bar.value() == 42
        assert window.status_label.text() == "Идёт очистка"


class TestCloseWhileBusy:
    """
    Закрытие окна во время оптимизации.

    Регрессия: окно закрывалось сразу, а сценарий продолжал работать —
    приложение меняло систему уже «после выхода», и процесс висел в фоне
    до конца операции.
    """

    @staticmethod
    def _close(window) -> QCloseEvent:
        """Имитирует нажатие «✕»: Qt так же присылает QCloseEvent."""
        event = QCloseEvent()
        window.closeEvent(event)
        return event

    def test_idle_window_closes_immediately(self, window):
        window.is_optimizing = False
        event = self._close(window)
        assert event.isAccepted()

    def test_close_is_refused_when_user_declines(self, window, monkeypatch):
        monkeypatch.setattr(window, "_confirm_stop_before_close", lambda: False)
        window.is_optimizing = True

        event = self._close(window)

        assert not event.isAccepted(), "окно не должно закрыться без согласия"
        assert window._cancel_requested is False, "оптимизация должна продолжиться"
        assert window._close_after_stop is False

    def test_close_requests_cancellation_and_waits(self, window, monkeypatch):
        monkeypatch.setattr(window, "_confirm_stop_before_close", lambda: True)
        window.is_optimizing = True

        event = self._close(window)

        # Окно не закрывается сразу: сначала сценарий должен остановиться.
        assert not event.isAccepted()
        assert window._cancel_requested is True
        assert window._close_after_stop is True

    def test_window_closes_after_scenario_stops(self, window, monkeypatch):
        monkeypatch.setattr(window, "_confirm_stop_before_close", lambda: True)
        window.is_optimizing = True
        self._close(window)

        closed: list[bool] = []
        monkeypatch.setattr(window, "close", lambda: closed.append(True))

        window._on_optimization_cancelled()
        QApplication.processEvents()

        assert closed, "после остановки окно должно закрыться само"
        assert window._close_after_stop is False

    def test_report_is_skipped_when_closing(self, window, monkeypatch):
        """Показывать отчёт в закрывающемся окне бессмысленно."""
        monkeypatch.setattr(window, "_confirm_stop_before_close", lambda: True)
        window.is_optimizing = True
        self._close(window)
        monkeypatch.setattr(window, "close", lambda: None)

        window._on_optimization_finished("## Отчёт")

        assert window.stacked_widget.currentWidget() is not window.results_page

    def test_cancel_button_is_disabled_while_stopping(self, window, monkeypatch):
        monkeypatch.setattr(window, "_confirm_stop_before_close", lambda: True)
        window.is_optimizing = True
        window.cancel_button.setEnabled(True)

        self._close(window)

        assert not window.cancel_button.isEnabled()
