"""Выбор языка и перевод видимых пользователю результатов."""

from pathlib import Path

from PyQt6.QtWidgets import QApplication

from winspector.core.modules.ai_communicator import AICommunicator
from winspector.gui import language
from winspector.gui.settings_dialog import SettingsDialog


def test_windows_language_is_used_once_then_manual_choice_wins(monkeypatch):
    language.settings_path().unlink(missing_ok=True)
    monkeypatch.setattr(language, "windows_language", lambda: "ru")
    assert language.get_language() == "ru"
    language.set_language("en")
    monkeypatch.setattr(language, "windows_language", lambda: "ru")
    assert language.get_language() == "en"


def test_settings_switch_language_immediately(monkeypatch, tmp_path: Path):
    app = QApplication.instance() or QApplication([])
    assert app is not None
    monkeypatch.setattr("winspector.gui.settings_dialog.quarantine.list_batches", lambda: [])
    monkeypatch.setattr("winspector.gui.settings_dialog.credentials.has_api_key", lambda: False)
    monkeypatch.setattr(
        "winspector.gui.settings_dialog.credentials.storage_path",
        lambda: tmp_path / "key.dat",
    )
    dialog = SettingsDialog()
    try:
        assert dialog.windowTitle().startswith("Настройки")
        assert dialog.gemini_status.text() == "Gemini не подключён"
        dialog.language_combo.setCurrentIndex(1)
        assert dialog.windowTitle().startswith("Settings")
        assert language.get_language() == "en"
        assert dialog.gemini_status.text() == "Gemini not connected"
        assert dialog.close_button.text() == "Close"
    finally:
        dialog.close()


def test_english_offline_report_and_prompt():
    summary = {"cleanup": {"deleted_files_count": 2, "cleaned_size_bytes": 1024}}
    report = AICommunicator.build_offline_report(summary, language="en")
    prompt = AICommunicator._create_report_prompt(
        AICommunicator.__new__(AICommunicator), summary, [], [], language="en"
    )
    assert "Optimization complete" in report
    assert "report in English" in prompt
