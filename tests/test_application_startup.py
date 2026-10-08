"""Запуск сразу открывает главное окно, независимо от настройки Gemini."""

import sys

import pytest

pytest.importorskip("PyQt6")
pytestmark = [pytest.mark.gui]

from PyQt6.QtWidgets import QApplication  # noqa: E402

from winspector import application  # noqa: E402


@pytest.mark.parametrize("has_key", [False, True])
def test_startup_does_not_open_key_dialog(monkeypatch, tmp_path, has_key):
    qapp = QApplication.instance() or QApplication([])
    monkeypatch.setattr(application, "QApplication", lambda _args: qapp)
    monkeypatch.setattr(application.credentials, "has_api_key", lambda: has_key)
    monkeypatch.setattr(application.Application, "_setup_logging", lambda self: None)
    monkeypatch.setattr(application.Application, "_check_admin_rights", lambda self: True)
    monkeypatch.setattr(application.Application, "_check_single_instance", lambda self: True)
    monkeypatch.setattr(application.Application, "_apply_styles", lambda self: None)
    monkeypatch.setattr(application.Application, "_set_app_icon", lambda self: None)
    monkeypatch.setattr(application.Application, "_set_app_user_model_id", lambda self: None)
    initialized = []
    monkeypatch.setattr(
        application.Application,
        "_initialize_core",
        lambda self: initialized.append(self.ai_enabled) or True,
    )
    monkeypatch.setattr(
        application.Application, "_initialize_gui", lambda self: initialized.append("gui")
    )
    original_hook = sys.excepthook
    try:
        app = application.Application({"assets": tmp_path})
        assert app.initialize()
        assert initialized == [has_key, "gui"]
    finally:
        sys.excepthook = original_hook
