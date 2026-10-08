"""Поведение окна настройки Gemini, независимое от его визуального оформления."""

from __future__ import annotations

import threading
import time

import pytest

pytest.importorskip("PyQt6", reason="PyQt6 не установлен")
pytestmark = [pytest.mark.gui]

from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtWidgets import QApplication, QLineEdit, QPushButton  # noqa: E402

from winspector.core.modules.ai_base import KeyCheckResult  # noqa: E402
from winspector.gui.api_key_dialog import ApiKeyDialog  # noqa: E402

FAKE_KEY = "test-only-fake-credential-0000000000"


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


class FakeChecker:
    """Заменяет запрос к Gemini: отвечает заданным результатом и считает вызовы."""

    def __init__(self) -> None:
        self.result = KeyCheckResult(True, "Ключ работает.")
        self.calls: list[str] = []
        self.release = threading.Event()
        self.release.set()

    def __call__(self, key: str) -> KeyCheckResult:
        self.calls.append(key)
        self.release.wait(timeout=5)
        return self.result


@pytest.fixture
def checker():
    return FakeChecker()


@pytest.fixture
def saved(monkeypatch):
    keys: list[str] = []
    monkeypatch.setattr("winspector.gui.api_key_dialog.credentials.save_api_key", keys.append)
    monkeypatch.setattr("winspector.core.modules.ai_base.AIBase.reset_client", lambda: None)
    return keys


@pytest.fixture
def dialog(qapp, monkeypatch, checker):
    monkeypatch.setattr("winspector.gui.api_key_dialog.credentials.is_supported", lambda: True)
    instance = ApiKeyDialog(first_run=True, key_checker=checker)
    yield instance
    instance.close()


def wait_for_check(dialog, qapp, timeout: float = 5.0) -> None:
    """Прокручивает цикл событий, пока окно не заберёт результат проверки."""
    deadline = time.monotonic() + timeout
    while dialog._pending_check is not None and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    assert dialog._pending_check is None, "проверка ключа не завершилась"


class TestAppearanceContract:
    def test_dialog_is_a_regular_window_with_clear_actions(self, dialog):
        # Системная рамка: окно двигается и закрывается как любое окно Windows.
        assert not dialog.windowFlags() & Qt.WindowType.FramelessWindowHint
        assert dialog.findChild(QPushButton, "PrimaryButton") is not None
        assert dialog.findChild(QPushButton, "SecondaryButton") is not None

    def test_key_is_masked_by_default(self, dialog):
        assert dialog.key_input.echoMode() == QLineEdit.EchoMode.Password


class TestInteraction:
    def test_reveal_button_updates_mode_and_caption(self, dialog):
        dialog.show_button.setChecked(True)
        assert dialog.key_input.echoMode() == QLineEdit.EchoMode.Normal
        assert dialog.show_button.toolTip() == "Скрыть ключ"

        dialog.show_button.setChecked(False)
        assert dialog.key_input.echoMode() == QLineEdit.EchoMode.Password
        assert dialog.show_button.toolTip() == "Показать ключ"

    def test_empty_key_shows_inline_error_without_clipping(self, dialog, qapp):
        dialog.key_input.clear()
        dialog._save()
        qapp.processEvents()

        assert dialog.status_label.isHidden() is False
        assert "Введите ключ" in dialog.status_label.text()
        assert dialog.height() >= dialog._content_height(dialog.width())

    def test_offline_choice_accepts_dialog(self, dialog):
        dialog._continue_offline()

        assert dialog.offline_chosen is True
        assert dialog.result() == dialog.DialogCode.Accepted

    def test_valid_key_is_checked_then_saved(self, dialog, qapp, monkeypatch, checker):
        saved: list[str] = []
        reset: list[bool] = []
        monkeypatch.setattr("winspector.gui.api_key_dialog.credentials.save_api_key", saved.append)
        monkeypatch.setattr(
            "winspector.core.modules.ai_base.AIBase.reset_client",
            lambda: reset.append(True),
        )
        dialog.key_input.setText(FAKE_KEY)

        dialog._save()
        wait_for_check(dialog, qapp)

        assert checker.calls == [FAKE_KEY]
        assert saved == [FAKE_KEY]
        assert reset == [True]
        assert dialog.offline_chosen is False
        assert dialog.result() == dialog.DialogCode.Accepted


class TestKeyCheck:
    def test_rejected_key_is_explained_and_not_saved(self, dialog, qapp, checker, saved):
        checker.result = KeyCheckResult(
            False, "Модель gemini-3.8-flash недоступна для этого ключа."
        )
        dialog.key_input.setText(FAKE_KEY)

        dialog._save()
        wait_for_check(dialog, qapp)

        assert saved == []
        assert "недоступна" in dialog.status_label.text()
        assert dialog.status_label.property("state") == "error"
        assert dialog.result() != dialog.DialogCode.Accepted
        assert dialog.save_button.isEnabled(), "после отказа ключ можно исправить"

    def test_controls_are_locked_while_checking(self, dialog, qapp, checker, saved):
        checker.release.clear()
        dialog.key_input.setText(FAKE_KEY)

        dialog._save()
        qapp.processEvents()

        assert dialog.save_button.isEnabled() is False
        assert dialog.key_input.isEnabled() is False
        assert dialog.status_label.property("state") == "info"

        dialog._save()  # повторное нажатие не запускает вторую проверку
        checker.release.set()
        wait_for_check(dialog, qapp)
        assert checker.calls == [FAKE_KEY]

    def test_choosing_offline_during_check_does_not_save_key(self, dialog, qapp, checker, saved):
        checker.release.clear()
        dialog.key_input.setText(FAKE_KEY)
        dialog._save()

        dialog._continue_offline()
        checker.release.set()
        deadline = time.monotonic() + 0.5
        while time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(0.01)

        assert saved == []
        assert dialog.offline_chosen is True

    def test_malformed_key_is_rejected_without_request(self, dialog, checker, saved):
        dialog.key_input.setText("ключ с пробелами")

        dialog._save()

        assert checker.calls == []
        assert "некорректным" in dialog.status_label.text()

    def test_checker_crash_is_reported_without_its_text(self, dialog, qapp, saved):
        def crash(key: str) -> KeyCheckResult:
            raise RuntimeError(f"внутри ключ {key}")

        dialog._key_checker = crash
        dialog.key_input.setText(FAKE_KEY)

        dialog._save()
        wait_for_check(dialog, qapp)

        assert saved == []
        assert "RuntimeError" in dialog.status_label.text()
        assert FAKE_KEY not in dialog.status_label.text()

    def test_quota_warning_still_saves_key(self, dialog, qapp, checker, saved, monkeypatch):
        shown: list[str] = []
        monkeypatch.setattr(
            "winspector.gui.message_dialog.notify",
            lambda _parent, _title, text, **_kwargs: shown.append(text),
        )
        checker.result = KeyCheckResult(True, "Ключ работает, но лимит исчерпан.", warning=True)
        dialog.key_input.setText(FAKE_KEY)

        dialog._save()
        wait_for_check(dialog, qapp)

        assert saved == [FAKE_KEY]
        assert shown == ["Ключ работает, но лимит исчерпан."]
