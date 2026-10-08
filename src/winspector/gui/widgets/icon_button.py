"""Круглая кнопка-значок: подсвечивается под курсором, фокус берёт только с клавиатуры."""

from __future__ import annotations

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtWidgets import QPushButton, QWidget

from ..theme import MUTED, TEXT, glyph_icon

SIZE = 36


class IconButton(QPushButton):
    """
    Кнопка из одного значка, например шестерёнка настроек.

    Мышью фокус не берёт: иначе после закрытия окна настроек фокус
    возвращался на кнопку, и её подсветка оставалась висеть. С клавиатуры
    (Tab) кнопка по-прежнему доступна и показывает рамку фокуса.
    """

    def __init__(self, glyph: str, glyph_size: int = 18, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("IconButton")
        self.setFixedSize(SIZE, SIZE)
        self.setIconSize(QSize(glyph_size, glyph_size))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self._icon = glyph_icon(glyph, glyph_size, MUTED)
        # Под курсором значок светлеет вместе с подложкой.
        self._hover_icon = glyph_icon(glyph, glyph_size, TEXT)
        self.setIcon(self._icon)

    def enterEvent(self, event) -> None:
        super().enterEvent(event)
        if self.isEnabled():
            self.setIcon(self._hover_icon)

    def leaveEvent(self, event) -> None:
        super().leaveEvent(event)
        self.setIcon(self._icon)

    def hideEvent(self, event) -> None:
        # Скрытая под курсором кнопка не получает leaveEvent.
        super().hideEvent(event)
        self.setIcon(self._icon)
