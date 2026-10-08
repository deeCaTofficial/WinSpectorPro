"""Проверки реальных действий и состояний окна настроек."""

import pytest

pytest.importorskip("PyQt6")
pytestmark = [pytest.mark.gui]

from PyQt6.QtWidgets import QApplication, QTabWidget  # noqa: E402

from winspector.gui.settings_dialog import SettingsDialog  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def dialog(qapp, monkeypatch, tmp_path):
    monkeypatch.setattr("winspector.gui.settings_dialog.quarantine.list_batches", lambda: [])
    monkeypatch.setattr(
        "winspector.gui.settings_dialog.credentials.storage_path",
        lambda: tmp_path / "credentials.dat",
    )
    monkeypatch.setattr("winspector.gui.settings_dialog.credentials.has_api_key", lambda: False)
    result = SettingsDialog()
    yield result
    result.close()


def test_settings_have_real_sections_and_close(dialog):
    tabs = dialog.findChild(QTabWidget, "SettingsTabs")
    assert [tabs.tabText(index) for index in range(tabs.count())] == ["Общие", "Gemini", "Карантин"]
    assert "не подключён" in dialog.gemini_status.text()
    assert not dialog.delete_key_button.isEnabled()
    dialog.accept()
    assert dialog.result() == dialog.DialogCode.Accepted


def test_gemini_connected_state(dialog, monkeypatch, tmp_path):
    monkeypatch.setattr("winspector.gui.settings_dialog.credentials.has_api_key", lambda: True)
    (tmp_path / "credentials.dat").write_bytes(b"not-a-real-key")
    dialog._refresh_key()
    assert dialog.gemini_status.text() == "Gemini подключён"
    assert dialog.delete_key_button.isEnabled()
    assert "Заменить" in dialog.edit_key_button.text()


def test_direct_gemini_entry_opens_gemini_tab(qapp, monkeypatch):
    monkeypatch.setattr("winspector.gui.settings_dialog.quarantine.list_batches", lambda: [])
    monkeypatch.setattr("winspector.gui.settings_dialog.credentials.has_api_key", lambda: False)
    settings = SettingsDialog(initial_tab=1)
    try:
        assert settings.findChild(QTabWidget, "SettingsTabs").currentIndex() == 1
    finally:
        settings.close()


def test_quarantine_batches_show_dates_and_need_a_selection(qapp, monkeypatch, tmp_path):
    batch = tmp_path / "20260922-235700"
    monkeypatch.setattr("winspector.gui.settings_dialog.quarantine.list_batches", lambda: [batch])
    monkeypatch.setattr("winspector.gui.settings_dialog.credentials.has_api_key", lambda: False)
    settings = SettingsDialog(initial_tab=2)
    try:
        item = settings.batches.item(0)
        assert item.text() == "22.09.2026  23:57"
        assert not settings.restore_button.isEnabled(), "без выбора восстанавливать нечего"
        settings.batches.setCurrentItem(item)
        assert settings.restore_button.isEnabled()
    finally:
        settings.close()


def test_empty_quarantine_explains_itself(qapp, monkeypatch):
    monkeypatch.setattr("winspector.gui.settings_dialog.quarantine.list_batches", lambda: [])
    monkeypatch.setattr("winspector.gui.settings_dialog.credentials.has_api_key", lambda: False)
    settings = SettingsDialog(initial_tab=2)
    try:
        assert settings.batches.isHidden()
        assert not settings.quarantine_empty.isHidden()
        assert settings.quarantine_empty.text()
        assert not settings.restore_button.isEnabled()
    finally:
        settings.close()
