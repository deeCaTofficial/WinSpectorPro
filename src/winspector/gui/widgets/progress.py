"""Индикаторы хода оптимизации: тонкая полоса и отметки этапов."""

from __future__ import annotations

from PyQt6.QtCore import QEasingCurve, QPointF, QRectF, Qt, QVariantAnimation
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QProgressBar, QWidget

from ..theme import ACCENT, BORDER, BORDER_STRONG


class LinearProgress(QProgressBar):
    """
    Тонкая полоса прогресса.

    Без известного объёма работы (диапазон 0..0) по дорожке бежит отрезок:
    ядро сообщает, какой этап начался, но не долю выполненной работы, и
    заполнять полосу по номерам этапов было бы неправдой.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setTextVisible(False)
        self.setFixedHeight(4)
        self._phase = 0.0
        self._animation = QVariantAnimation(self)
        self._animation.setStartValue(0.0)
        self._animation.setEndValue(1.0)
        self._animation.setDuration(1500)
        self._animation.setLoopCount(-1)
        self._animation.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._animation.valueChanged.connect(self._advance)

    def _advance(self, value: object) -> None:
        self._phase = float(value)  # type: ignore[arg-type]
        self.update()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._animation.start()

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self._animation.stop()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        track = QRectF(self.rect())
        radius = track.height() / 2
        painter.setBrush(QColor(BORDER))
        painter.drawRoundedRect(track, radius, radius)

        span = self.maximum() - self.minimum()
        if span <= 0:
            length = track.width() * 0.32
            left = -length + (track.width() + length) * self._phase
            chunk = QRectF(left, 0, length, track.height()).intersected(track)
        else:
            share = (self.value() - self.minimum()) / span
            chunk = QRectF(0, 0, track.width() * share, track.height())
        if chunk.width() > 0:
            painter.setBrush(QColor(ACCENT))
            painter.drawRoundedRect(chunk, radius, radius)


class StepIndicator(QWidget):
    """Отметка этапа: пустой круг, текущий этап или выполненный с галочкой."""

    SIZE = 20

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(self.SIZE, self.SIZE)
        self._state = "pending"

    def set_state(self, state: str) -> None:
        if state != self._state:
            self._state = state
            self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        center = QPointF(self.SIZE / 2, self.SIZE / 2)
        radius = self.SIZE / 2 - 1.5

        if self._state == "done":
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(ACCENT))
            painter.drawEllipse(center, radius + 0.75, radius + 0.75)
            check = QPainterPath(QPointF(5.8, 10.2))
            check.lineTo(8.8, 13.0)
            check.lineTo(14.2, 7.4)
            pen = QPen(QColor("#FFFFFF"), 1.8)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(check)
            return

        active = self._state == "active"
        painter.setPen(QPen(QColor(ACCENT if active else BORDER_STRONG), 1.5))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(center, radius, radius)
        if active:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(ACCENT))
            painter.drawEllipse(center, 4.0, 4.0)
