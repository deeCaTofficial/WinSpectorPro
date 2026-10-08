"""Значок из шрифта Windows, по желанию — в скруглённой цветной плашке."""

from __future__ import annotations

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import QWidget

from ..theme import icon_font


def theme_color(value: str) -> QColor:
    """QColor из токена темы, в том числе из записи rgba(r, g, b, a)."""
    if value.startswith("rgba("):
        red, green, blue, alpha = (part.strip() for part in value[5:-1].split(","))
        return QColor(int(red), int(green), int(blue), round(float(alpha) * 255))
    return QColor(value)


class GlyphBadge(QWidget):
    """
    Значок, нарисованный шрифтом: чёткий при любом масштабе экрана.

    Без `background` это просто значок нужного цвета, например в заголовке
    карточки; с ним — значок в плашке, как в окнах сообщений.
    """

    def __init__(
        self,
        glyph: str,
        color: str,
        background: str | None = None,
        *,
        size: int = 40,
        glyph_size: int = 20,
        radius: int = 12,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.glyph = glyph
        self._color = color
        self._background = background
        self._glyph_size = glyph_size
        self._radius = radius
        self.setFixedSize(size, size)

    def set_colors(self, color: str, background: str | None = None) -> None:
        self._color, self._background = color, background
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._background is not None:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(theme_color(self._background))
            painter.drawRoundedRect(QRectF(self.rect()), self._radius, self._radius)
        font = icon_font(self._glyph_size)
        if font is not None:
            painter.setFont(font)
            painter.setPen(theme_color(self._color))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.glyph)
        painter.end()
