"""
Сообщения и подтверждения в оформлении приложения вместо QMessageBox.

Стандартное окно Qt выглядит чужим: крупный системный значок, текст одним
абзацем и кнопки «Да/Нет», по которым не понять, что произойдёт. Здесь —
значок в мягкой цветной плашке, короткий заголовок, пояснение под ним и
кнопки с глаголами («Остановить и закрыть», «Продолжить»).

Для необратимых действий кнопка красная, а Enter и Esc выбирают безопасный
вариант: случайное нажатие ничего не сломает.
"""

from __future__ import annotations

import contextlib
import ctypes
import sys
from typing import Literal

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from .. import APP_NAME
from .language import get_language, localize
from .theme import (
    ACCENT_SOFT,
    ACCENT_TEXT,
    DANGER,
    DANGER_SOFT,
    WARNING,
    WARNING_SOFT,
)
from .widgets.glyph_badge import GlyphBadge

Kind = Literal["info", "question", "warning", "error"]

WIDTH = 440
# Значок, его цвет и подложка для каждого вида сообщения.
_BADGES: dict[str, tuple[str, str, str]] = {
    "info": ("\ue946", ACCENT_TEXT, ACCENT_SOFT),
    "question": ("\ue9ce", ACCENT_TEXT, ACCENT_SOFT),
    "warning": ("\ue7ba", WARNING, WARNING_SOFT),
    "error": ("\uea39", DANGER, DANGER_SOFT),
}

# Системный звук, как у стандартных окон Windows с предупреждением и ошибкой.
_BEEPS = {"warning": 0x30, "error": 0x10}


class MessageDialog(QDialog):
    """Сообщение с одной кнопкой или подтверждение с двумя."""

    def __init__(
        self,
        parent: QWidget | None,
        title: str,
        text: str = "",
        *,
        kind: Kind = "info",
        accept_text: str,
        reject_text: str | None = None,
        destructive: bool = False,
    ) -> None:
        super().__init__(parent)
        self.kind = kind
        # Заголовок окна — название приложения: суть уже сказана внутри.
        self.setWindowTitle(APP_NAME)
        self.setFixedWidth(WIDTH)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 20)
        layout.setSpacing(22)

        body = QHBoxLayout()
        body.setSpacing(16)
        self.badge = GlyphBadge(*_BADGES[kind])
        body.addWidget(self.badge, 0, Qt.AlignmentFlag.AlignTop)
        texts = QVBoxLayout()
        texts.setSpacing(6)
        self.title_label = QLabel(title)
        self.title_label.setObjectName("MessageTitle")
        self.title_label.setWordWrap(True)
        self.title_label.setTextFormat(Qt.TextFormat.PlainText)
        texts.addWidget(self.title_label)
        self.text_label = QLabel(text)
        self.text_label.setObjectName("MessageText")
        self.text_label.setWordWrap(True)
        # Текст ошибок приходит извне: без разметки и с возможностью скопировать.
        self.text_label.setTextFormat(Qt.TextFormat.PlainText)
        self.text_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.text_label.setVisible(bool(text))
        texts.addWidget(self.text_label)
        body.addLayout(texts, 1)
        layout.addLayout(body)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addStretch()
        self.reject_button: QPushButton | None = None
        if reject_text is not None:
            self.reject_button = QPushButton(reject_text)
            self.reject_button.clicked.connect(self.reject)
            buttons.addWidget(self.reject_button)
        self.accept_button = QPushButton(accept_text)
        self.accept_button.setObjectName("DangerAction" if destructive else "PrimaryButton")
        self.accept_button.clicked.connect(self.accept)
        buttons.addWidget(self.accept_button)
        layout.addLayout(buttons)

        # Enter нажимает безопасную кнопку, если действие необратимо.
        default = self.reject_button if destructive and self.reject_button else self.accept_button
        for button in (self.reject_button, self.accept_button):
            if button is not None:
                button.setAutoDefault(button is default)
                button.setDefault(button is default)
                button.setMinimumWidth(96)
        default.setFocus()

        self.ensurePolished()
        self.setFixedHeight(self._content_height())

    def _content_height(self) -> int:
        """Высота под текст при фиксированной ширине: без пустот и обрезки."""
        layout = self.layout()
        assert layout is not None
        layout.activate()
        return layout.totalHeightForWidth(WIDTH)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        beep = _BEEPS.get(self.kind)
        if beep is not None and sys.platform == "win32":
            with contextlib.suppress(AttributeError, OSError):
                ctypes.windll.user32.MessageBeep(beep)


def confirm(
    parent: QWidget | None,
    title: str,
    text: str = "",
    *,
    accept: str,
    reject: str | None = None,
    destructive: bool = False,
    kind: Kind = "question",
    language: str | None = None,
) -> bool:
    """Спрашивает подтверждение; True — если выбрано действие `accept`."""
    language = language or get_language()
    dialog = MessageDialog(
        parent,
        title,
        text,
        kind=kind,
        accept_text=accept,
        reject_text=reject or localize(language, "Отмена", "Cancel"),
        destructive=destructive,
    )
    return dialog.exec() == QDialog.DialogCode.Accepted


def notify(
    parent: QWidget | None,
    title: str,
    text: str = "",
    *,
    kind: Kind = "info",
    language: str | None = None,
) -> None:
    """Сообщение с одной кнопкой."""
    language = language or get_language()
    MessageDialog(
        parent, title, text, kind=kind, accept_text=localize(language, "Понятно", "OK")
    ).exec()
