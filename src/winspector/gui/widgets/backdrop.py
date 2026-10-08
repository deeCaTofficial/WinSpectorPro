"""
Фон главного окна: сетка точек, по которой расходится волна.

Ровная сетка едва заметных точек убирает ощущение плоского тёмного экрана, а
мягкая волна, которая время от времени расходится от логотипа (во время
работы — от полосы прогресса), напоминает сканирование. Во время оптимизации
волны идут чаще и быстрее, на экране отчёта фон замирает.

Сетка рисуется один раз в картинку под размер окна; в каждом кадре
перерисовываются только точки под фронтом волны. Точки заранее отсортированы
по расстоянию от источника, поэтому нужные находятся двоичным поиском, а сами
точки под волной — готовые картинки на несколько уровней яркости: сглаженный
круг на каждую точку в каждом кадре обходился в разы дороже.

Анимация стоит, пока окно свёрнуто или скрыто, и не запускается вовсе, если
в Windows отключена анимация («Специальные возможности → Визуальные эффекты»).
"""

from __future__ import annotations

import bisect
import ctypes
import math
import sys

from PyQt6.QtCore import QElapsedTimer, QPoint, QPointF, Qt, QTimer
from PyQt6.QtGui import QColor, QPainter, QPixmap
from PyQt6.QtWidgets import QWidget

from ..theme import ACCENT_TEXT, BG

_FRAME_MS = 33  # около 30 кадров в секунду

_STEP = 26  # шаг сетки, px
_DOT_RADIUS = 1.0
_DOT_ALPHA = 13
# Под фронтом волны точка подрастает и окрашивается в акцентный цвет.
_WAVE_RADIUS = 1.8
_WAVE_ALPHA = 70
_WAVE_WIDTH = 48.0  # полуширина фронта (сигма), px
_WAVE_REACH = int(2.5 * _WAVE_WIDTH)  # дальше фронт уже почти не виден
_WAVE_LEVELS = 10  # ступени яркости точки под волной
# Сила волны по расстоянию до фронта с шагом 1 px — вместо exp в каждом кадре.
_WAVE_PROFILE = [
    math.exp(-(offset * offset) / (2 * _WAVE_WIDTH**2)) for offset in range(_WAVE_REACH + 1)
]

# Скорость фронта и пауза между волнами: в покое и во время работы.
_IDLE_SPEED, _ACTIVE_SPEED = 110.0, 190.0  # px/с
_IDLE_INTERVAL, _ACTIVE_INTERVAL = 6.5, 2.2  # с
# Первая волна — вскоре после открытия окна, а не через полный интервал.
_FIRST_WAVE_DELAY = 1.2

_SPI_GETCLIENTAREAANIMATION = 0x1042


def animations_enabled() -> bool:
    """Разрешена ли анимация интерфейса в настройках Windows."""
    if sys.platform != "win32":
        return True
    try:
        value = ctypes.c_int(1)
        if ctypes.windll.user32.SystemParametersInfoW(
            _SPI_GETCLIENTAREAANIMATION, 0, ctypes.byref(value), 0
        ):
            return bool(value.value)
    except (AttributeError, OSError):
        pass
    return True


def _wave_color(strength: float) -> QColor:
    """Цвет точки под волной: от белёсого к акцентному по силе волны."""
    accent = QColor(ACCENT_TEXT)
    return QColor(
        round(255 + (accent.red() - 255) * strength),
        round(255 + (accent.green() - 255) * strength),
        round(255 + (accent.blue() - 255) * strength),
        round(_DOT_ALPHA + (_WAVE_ALPHA - _DOT_ALPHA) * strength),
    )


class DotGridBackground(QWidget):
    """Фон с сеткой точек и волнами; дочерние виджеты рисуются поверх."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._animated = animations_enabled()
        self._paused = False
        self._active = False
        self._origin_widget: QWidget | None = None
        # Радиусы фронтов волн, которые сейчас расходятся, px.
        self.waves: list[float] = []
        self._until_next_wave = _FIRST_WAVE_DELAY
        self._grid: QPixmap | None = None
        self._grid_key: tuple | None = None
        self._dots: list[tuple[float, float, float]] = []  # (расстояние, x, y)
        self._distances: list[float] = []
        self._sprites: list[QPixmap] = []
        self._sprite_ratio = 0.0
        self._frame_clock = QElapsedTimer()
        self._timer = QTimer(self)
        self._timer.setInterval(_FRAME_MS)
        self._timer.timeout.connect(self._tick)

    @property
    def running(self) -> bool:
        return self._timer.isActive()

    def set_origin(self, widget: QWidget | None) -> None:
        """Откуда расходятся волны; без виджета — из центра окна."""
        self._origin_widget = widget
        self.update()

    def set_active(self, active: bool) -> None:
        """Во время оптимизации волны идут чаще и быстрее."""
        self._active = active
        if active:
            self._until_next_wave = min(self._until_next_wave, 0.3)

    def set_paused(self, paused: bool) -> None:
        """Замирание, например пока человек читает отчёт."""
        self._paused = paused
        self._sync_timer()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._sync_timer()

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self._sync_timer()

    def _sync_timer(self) -> None:
        should_run = self._animated and not self._paused and self.isVisible()
        if should_run and not self._timer.isActive():
            self._frame_clock.start()
            self._timer.start()
        elif not should_run and self._timer.isActive():
            self._timer.stop()

    def _tick(self) -> None:
        dt = min(self._frame_clock.restart() / 1000, 0.1)
        window = self.window()
        if window is not None and window.isMinimized():
            return
        self.advance(dt)

    def advance(self, dt: float) -> None:
        """Сдвигает волны на `dt` секунд и запускает новые по расписанию."""
        speed = _ACTIVE_SPEED if self._active else _IDLE_SPEED
        reach = math.hypot(self.width(), self.height()) + _WAVE_REACH
        self.waves = [radius + speed * dt for radius in self.waves if radius < reach]
        self._until_next_wave -= dt
        if self._until_next_wave <= 0:
            self.waves.append(0.0)
            self._until_next_wave = _ACTIVE_INTERVAL if self._active else _IDLE_INTERVAL
        if self.waves:
            self.update()

    def _origin(self) -> QPointF:
        widget = self._origin_widget
        if widget is not None and widget.isVisible():
            center = widget.mapTo(self, QPoint(widget.width() // 2, widget.height() // 2))
            return QPointF(center)
        return QPointF(self.width() / 2, self.height() * 0.42)

    def _prepare_grid(self, origin: QPointF) -> None:
        """Сетка в картинку и точки по расстоянию — при смене размера или источника."""
        ratio = self.devicePixelRatioF()
        key = (self.width(), self.height(), round(origin.x()), round(origin.y()), ratio)
        if key == self._grid_key:
            return
        self._grid_key = key
        width, height = self.width(), self.height()
        pixmap = QPixmap(max(1, round(width * ratio)), max(1, round(height * ratio)))
        pixmap.setDevicePixelRatio(ratio)
        pixmap.fill(QColor(BG))
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255, _DOT_ALPHA))
        # Сетка по центру окна: поля по краям одинаковые.
        start_x = (width % _STEP) / 2 + _STEP / 2
        start_y = (height % _STEP) / 2 + _STEP / 2
        dots = []
        y = start_y
        while y < height:
            x = start_x
            while x < width:
                painter.drawEllipse(QPointF(x, y), _DOT_RADIUS, _DOT_RADIUS)
                dots.append((math.hypot(x - origin.x(), y - origin.y()), x, y))
                x += _STEP
            y += _STEP
        painter.end()
        dots.sort()
        self._grid = pixmap
        self._dots = dots
        self._distances = [dot[0] for dot in dots]

    def _wave_sprites(self) -> list[QPixmap]:
        """Точка под волной на каждую ступень яркости, под масштаб экрана."""
        ratio = self.devicePixelRatioF()
        if self._sprite_ratio != ratio:
            self._sprite_ratio = ratio
            side = math.ceil(_WAVE_RADIUS * 2 + 2)
            sprites = []
            for level in range(_WAVE_LEVELS + 1):
                strength = level / _WAVE_LEVELS
                pixmap = QPixmap(round(side * ratio), round(side * ratio))
                pixmap.setDevicePixelRatio(ratio)
                pixmap.fill(Qt.GlobalColor.transparent)
                painter = QPainter(pixmap)
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(_wave_color(strength))
                size = _DOT_RADIUS + (_WAVE_RADIUS - _DOT_RADIUS) * strength
                painter.drawEllipse(QPointF(side / 2, side / 2), size, size)
                painter.end()
                sprites.append(pixmap)
            self._sprites = sprites
        return self._sprites

    def paintEvent(self, event) -> None:
        origin = self._origin()
        self._prepare_grid(origin)
        painter = QPainter(self)
        if self._grid is not None:
            painter.drawPixmap(0, 0, self._grid)
        if self.waves:
            sprites = self._wave_sprites()
            half = math.ceil(_WAVE_RADIUS * 2 + 2) / 2
            fade_distance = math.hypot(self.width(), self.height()) * 0.8
            levels: dict[int, int] = {}
            distances = self._distances
            for radius in self.waves:
                fade = 1 - radius / fade_distance
                if fade * _WAVE_LEVELS < 1:
                    continue  # волна уже погасла
                low = bisect.bisect_left(distances, radius - _WAVE_REACH)
                high = bisect.bisect_right(distances, radius + _WAVE_REACH)
                for index in range(low, high):
                    strength = _WAVE_PROFILE[int(abs(distances[index] - radius))] * fade
                    level = int(strength * _WAVE_LEVELS)
                    # Две волны в одной точке не складываются в слишком яркую.
                    if level and level > levels.get(index, 0):
                        levels[index] = level
            for index, level in levels.items():
                _, x, y = self._dots[index]
                painter.drawPixmap(QPointF(x - half, y - half), sprites[level])
        painter.end()
