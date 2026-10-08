"""Сообщения и подтверждения в оформлении приложения."""

import pytest

pytest.importorskip("PyQt6")
pytestmark = [pytest.mark.gui]

from PyQt6.QtWidgets import QDialog  # noqa: E402

from winspector.gui import message_dialog  # noqa: E402
from winspector.gui.message_dialog import MessageDialog  # noqa: E402

LONG_TEXT = "Уже сделанные изменения сохранятся. " * 12


def _dialog(**kwargs):
    options = {"kind": "warning", "accept_text": "Остановить", "reject_text": "Продолжить"}
    options.update(kwargs)
    return MessageDialog(None, "Остановить оптимизацию?", LONG_TEXT, **options)


def test_destructive_action_is_red_and_enter_picks_safe_button(qapp):
    dialog = _dialog(destructive=True)
    assert dialog.accept_button.objectName() == "DangerAction"
    assert dialog.reject_button is not None
    assert dialog.reject_button.isDefault()
    assert not dialog.accept_button.isDefault()


def test_ordinary_confirmation_defaults_to_action(qapp):
    dialog = _dialog(kind="question")
    assert dialog.accept_button.objectName() == "PrimaryButton"
    assert dialog.accept_button.isDefault()


def test_text_is_plain_and_fits(qapp):
    dialog = MessageDialog(
        None, "Ошибка", "<b>не разметка</b>\n" + LONG_TEXT, kind="error", accept_text="OK"
    )
    assert dialog.text_label.text().startswith("<b>")
    assert dialog.reject_button is None
    # Высота подогнана под текст: длинное пояснение не обрезается.
    needed = dialog.layout().totalHeightForWidth(message_dialog.WIDTH)
    assert dialog.height() == needed
    short = MessageDialog(None, "Готово", "", accept_text="OK")
    assert short.text_label.isHidden()
    assert short.height() < dialog.height()


def test_window_title_is_app_name(qapp):
    from winspector import APP_NAME

    assert _dialog().windowTitle() == APP_NAME


@pytest.mark.parametrize(
    ("code", "expected"),
    [(QDialog.DialogCode.Accepted, True), (QDialog.DialogCode.Rejected, False)],
)
def test_confirm_returns_choice(qapp, monkeypatch, code, expected):
    created = []

    def fake_exec(self):
        created.append(self)
        return code

    monkeypatch.setattr(MessageDialog, "exec", fake_exec)
    result = message_dialog.confirm(None, "Удалить?", accept="Удалить", language="ru")
    assert result is expected
    assert created[0].reject_button.text() == "Отмена"


def test_notify_has_single_localized_button(qapp, monkeypatch):
    created = []
    monkeypatch.setattr(MessageDialog, "exec", lambda self: created.append(self) or 0)
    message_dialog.notify(None, "Saved", kind="info", language="en")
    assert created[0].accept_button.text() == "OK"
    assert created[0].reject_button is None
    assert created[0].kind == "info"
