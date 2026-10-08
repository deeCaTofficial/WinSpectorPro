"""
Строка-карточка: значок, название с пояснением в одну строку и действия справа.

Из таких строк собраны окно настроек и окно ключа Gemini, поэтому оба
читаются одинаково: где искать настройку, что она значит и что с ней сделать.
"""

from __future__ import annotations

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ..theme import MUTED, glyph_icon

ICON_SIZE = 20
# Одна ширина у всех действий в строках: правый край карточек ровный.
CONTROL_WIDTH = 150


def set_row_icon(icon: QLabel, glyph: str, color: str = MUTED) -> None:
    icon.setPixmap(
        glyph_icon(glyph, ICON_SIZE, color, color).pixmap(QSize(ICON_SIZE, ICON_SIZE), 2.0)
    )


def row_icon(row: QFrame) -> QLabel | None:
    return row.findChild(QLabel, "RowIcon")


def setting_row(glyph: str, *controls: QWidget) -> tuple[QFrame, QLabel, QLabel]:
    """Возвращает строку и её подписи: название и пояснение заполняет вызывающий."""
    row = QFrame()
    row.setObjectName("SettingRow")
    layout = QHBoxLayout(row)
    layout.setContentsMargins(16, 12, 14, 12)
    layout.setSpacing(14)
    icon = QLabel()
    icon.setObjectName("RowIcon")
    icon.setFixedSize(ICON_SIZE, ICON_SIZE)
    set_row_icon(icon, glyph)
    title = QLabel()
    title.setObjectName("RowTitle")
    subtitle = QLabel()
    subtitle.setObjectName("RowSubtitle")
    subtitle.setWordWrap(True)
    texts = QVBoxLayout()
    texts.setSpacing(2)
    texts.addWidget(title)
    texts.addWidget(subtitle)
    layout.addWidget(icon, 0, Qt.AlignmentFlag.AlignVCenter)
    layout.addLayout(texts, 1)
    for control in controls:
        control.setMinimumWidth(CONTROL_WIDTH)
        layout.addWidget(control, 0, Qt.AlignmentFlag.AlignVCenter)
    return row, title, subtitle
