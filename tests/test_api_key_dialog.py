"""Поведение окна настройки Gemini, независимое от его визуального оформления."""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6", reason="PyQt6 не установлен")
pytestmark = [pytest.mark.gui]

from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtWidgets import QApplication, QLineEdit, QPushButton  # noqa: E402

from winspector.gui.api_key_dialog import ApiKeyDialog  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def dialog(qapp, monkeypatch):
    monkeypatch.setattr("winspector.gui.api_key_dialog.credentials.is_supported", lambda: True)
    instance = ApiKeyDialog(first_run=True)
    yield instance
    instance.close()


class TestAppearanceContract:
    def test_dialog_uses_frameless_translucent_surface(self, dialog):
        assert dialog.windowFlags() & Qt.WindowType.FramelessWindowHint
        assert dialog.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        assert dialog.findChild(QPushButton, "PrimaryButton") is not None
        assert dialog.findChild(QPushButton, "SecondaryButton") is not None

    def test_key_is_masked_by_default(self, dialog):
        assert dialog.key_input.echoMode() == QLineEdit.EchoMode.Password


class TestInteraction:
    def test_reveal_button_updates_mode_and_caption(self, dialog):
        dialog.show_button.setChecked(True)
        assert dialog.key_input.echoMode() == QLineEdit.EchoMode.Normal
        assert dialog.show_button.text() == "Скрыть"

        dialog.show_button.setChecked(False)
        assert dialog.key_input.echoMode() == QLineEdit.EchoMode.Password
        assert dialog.show_button.text() == "Показать"

    def test_empty_key_shows_inline_error_without_clipping(self, dialog, qapp):
        dialog.key_input.clear()
        dialog._save()
        qapp.processEvents()

        assert dialog.status_label.isHidden() is False
        assert "Введите ключ" in dialog.status_label.text()
        assert dialog.height() >= dialog.sizeHint().height()

    def test_offline_choice_accepts_dialog(self, dialog):
        dialog._continue_offline()

        assert dialog.offline_chosen is True
        assert dialog.result() == dialog.DialogCode.Accepted

    def test_valid_key_is_saved_and_enables_ai(self, dialog, monkeypatch):
        saved: list[str] = []
        reset: list[bool] = []
        monkeypatch.setattr(
            "winspector.gui.api_key_dialog.credentials.save_api_key", saved.append
        )
        monkeypatch.setattr(
            "winspector.core.modules.ai_base.AIBase.reset_client",
            lambda: reset.append(True),
        )
        dialog.key_input.setText("test-only-fake-credential-0000000000")

        dialog._save()

        assert saved == ["test-only-fake-credential-0000000000"]
        assert reset == [True]
        assert dialog.offline_chosen is False
        assert dialog.result() == dialog.DialogCode.Accepted
