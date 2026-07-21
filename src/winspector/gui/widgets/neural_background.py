# src/winspector/gui/widgets/neural_background.py
"""
Анимированный фон: частицы, соединённые линиями, с отталкиванием от курсора.

Физика намеренно работает на обычных числах Python, а не на `QPointF`.
Каждое обращение вида `point.x()` — это переход Python -> C++ через sip, и на
150 частицах с несколькими проходами по кадру такие переходы складывались в
основную стоимость анимации. Объекты Qt создаются только в момент отрисовки.
"""

import math
import random

from PyQt6.QtCore import QPoint, QPointF, QRectF, Qt, QTimer
from PyQt6.QtGui import (
    QColor,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPen,
    QResizeEvent,
    QShowEvent,
)
from PyQt6.QtWidgets import QWidget


class Particle:
    """Частица фона. Координаты и скорость — простые числа."""

    __slots__ = ("mass", "parallax_factor", "size", "vx", "vy", "x", "y")

    def __init__(
        self,
        x: float,
        y: float,
        vx: float,
        vy: float,
        size: float,
        parallax_factor: float,
        mass: float,
    ) -> None:
        self.x = x
        self.y = y
        self.vx = vx
        self.vy = vy
        self.size = size
        self.parallax_factor = parallax_factor
        self.mass = mass


class NeuralBackgroundWidget(QWidget):
    """Анимированный фон с эффектом отталкивания частиц от курсора."""

    PARTICLE_COUNT: int = 150
    CONNECTION_DISTANCE: float = 100.0
    BASE_SPEED: float = 0.25
    MIN_SPEED: float = 0.15
    MAX_SPEED: float = 0.35
    MAX_CONNECTIONS: int = 3
    CONNECTION_STICKINESS: float = 0.85
    CONNECTION_BREAK_FACTOR: float = 1.1

    REPULSION_RADIUS: float = 100.0
    REPULSION_STRENGTH: float = 2.0
    WALL_REBOUND_FACTOR: float = 1.1

    CORNER_RADIUS: float = 20.0
    FADE_SPEED: float = 0.01

    FRAME_INTERVAL_MS: int = 30

    BG_COLOR = QColor(33, 37, 43)
    PARTICLE_COLOR = QColor(82, 152, 215, 150)
    LINE_BASE_COLOR = QColor(82, 152, 215)

    # Оттенок частиц и линий плавно колеблется. Непрерывный расчёт цвета на
    # каждую частицу в каждом кадре — это тысячи вызовов QColor.fromHslF в
    # секунду, поэтому оттенок квантуется и цвета берутся из таблицы.
    _HUE_STEPS: int = 64
    _BASE_HUE: float = 0.62
    _HUE_RANGE: float = 0.08

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)

        self.particles: list[Particle] = []
        self.mouse_x: float = -1.0
        self.mouse_y: float = -1.0

        self.time_counter = 0.0

        # Активные и затухающие соединения: (i, j) -> (непрозрачность, dist^2).
        self.active_connections: dict[tuple[int, int], tuple[float, float]] = {}

        # Пространственная сетка вместо перебора всех пар частиц.
        self.grid: dict[tuple[int, int], list[int]] = {}
        self.grid_cell_size: float = self.CONNECTION_DISTANCE

        self.connection_distance_sq: float = self.CONNECTION_DISTANCE**2
        self.break_distance_sq: float = self.connection_distance_sq * (
            self.CONNECTION_BREAK_FACTOR**2
        )
        self.min_speed_sq: float = self.MIN_SPEED**2
        self.max_speed_sq: float = self.MAX_SPEED**2
        self.corner_centers: list[tuple[float, float]] = []

        self._line_colors = self._build_palette(saturation=0.8, lightness=0.6)
        self._particle_colors = self._build_palette(saturation=0.9, lightness=0.7, alpha=0.8)

        self.animation_timer = QTimer(self)
        self.animation_timer.timeout.connect(self.update_particles)
        # Таймер намеренно не запускается здесь: до вызова start_animation
        # он лишь будил бы приложение десятки раз в секунду впустую.

        self.setMouseTracking(True)
        self.is_animation_running = False

    # --- Палитра ----------------------------------------------------------

    @classmethod
    def _build_palette(
        cls, saturation: float, lightness: float, alpha: float = 1.0
    ) -> list[QColor]:
        """Готовит таблицу цветов для всех оттенков колебания."""
        return [
            QColor.fromHslF(
                cls._BASE_HUE + cls._HUE_RANGE * (2.0 * step / (cls._HUE_STEPS - 1) - 1.0),
                saturation,
                lightness,
                alpha,
            )
            for step in range(cls._HUE_STEPS)
        ]

    @classmethod
    def _hue_index(cls, oscillation: float) -> int:
        """Переводит колебание из диапазона [-1, 1] в индекс таблицы цветов."""
        index = int((oscillation + 1.0) * 0.5 * (cls._HUE_STEPS - 1))
        return min(cls._HUE_STEPS - 1, max(0, index))

    # --- Частицы ----------------------------------------------------------

    def _create_particle(self) -> Particle:
        """Создаёт частицу со случайными параметрами."""
        size = random.uniform(2, 4.5)
        return Particle(
            x=self.width() * (0.05 + 0.9 * random.random()),
            y=self.height() * (0.05 + 0.9 * random.random()),
            vx=random.uniform(-self.BASE_SPEED, self.BASE_SPEED),
            vy=random.uniform(-self.BASE_SPEED, self.BASE_SPEED),
            size=size,
            parallax_factor=random.uniform(0.1, 0.5),
            mass=size * size,
        )

    def init_particles(self) -> None:
        """Пересоздаёт частицы и сбрасывает соединения."""
        if not self.isVisible() or self.width() == 0 or self.height() == 0:
            return

        w, h, r = self.width(), self.height(), self.CORNER_RADIUS
        self.corner_centers = [(r, r), (w - r, r), (w - r, h - r), (r, h - r)]

        self.particles = [self._create_particle() for _ in range(self.PARTICLE_COUNT)]
        # Иначе после разворачивания окна остаются «призрачные» линии
        # между частицами, которых уже нет.
        self.active_connections.clear()

        if self.mouse_x < 0:
            self.mouse_x = w / 2
            self.mouse_y = h / 2

        self.update()

    def _rebuild_grid(self) -> None:
        """Раскладывает частицы по ячейкам сетки."""
        grid = self.grid
        grid.clear()
        cell_size = self.grid_cell_size
        if not cell_size or not self.particles:
            return

        for index, particle in enumerate(self.particles):
            key = (int(particle.x / cell_size), int(particle.y / cell_size))
            bucket = grid.get(key)
            if bucket is None:
                grid[key] = [index]
            else:
                bucket.append(index)

    def _adjacent_indices(self, cell_x: int, cell_y: int) -> list[int]:
        """Индексы частиц в указанной ячейке и восьми соседних."""
        grid = self.grid
        indices: list[int] = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                bucket = grid.get((cell_x + dx, cell_y + dy))
                if bucket:
                    indices.extend(bucket)
        return indices

    # --- Кадр -------------------------------------------------------------

    def update_particles(self) -> None:
        """Считает один кадр анимации."""
        if not self.is_animation_running:
            return

        particles = self.particles
        if not particles:
            return

        self.time_counter += 0.03
        count = len(particles)
        w, h, r = self.width(), self.height(), self.CORNER_RADIUS

        self._move_and_repel(particles)
        self._rebuild_grid()

        # Соседство считается один раз за кадр и переиспользуется на этапах
        # связей и столкновений: раньше сетка опрашивалась дважды.
        neighbourhood = self._collect_neighbourhood(particles, count)

        self._update_connections(neighbourhood, count)
        self._resolve_collisions(particles, neighbourhood)
        self._bounce_off_walls(particles, w, h, r)
        self._clamp_speeds(particles)

        self.update()

    def _move_and_repel(self, particles: list[Particle]) -> None:
        """Смещает частицы и отталкивает их от курсора."""
        mouse_x, mouse_y = self.mouse_x, self.mouse_y
        radius = self.REPULSION_RADIUS
        radius_sq = radius * radius
        strength = self.REPULSION_STRENGTH
        has_mouse = mouse_x > 0

        for particle in particles:
            particle.x += particle.vx
            particle.y += particle.vy

            if not has_mouse:
                continue

            dx = particle.x - mouse_x
            dy = particle.y - mouse_y
            dist_sq = dx * dx + dy * dy
            if 1e-6 < dist_sq < radius_sq:
                dist = math.sqrt(dist_sq)
                force = (1.0 - dist / radius) * strength / dist
                particle.x += dx * force
                particle.y += dy * force

    def _collect_neighbourhood(
        self, particles: list[Particle], count: int
    ) -> list[list[tuple[float, int]]]:
        """Для каждой частицы собирает соседей в радиусе связи."""
        neighbourhood: list[list[tuple[float, int]]] = [[] for _ in range(count)]
        cell_size = self.grid_cell_size
        break_distance_sq = self.break_distance_sq

        for i in range(count):
            p1 = particles[i]
            x1, y1 = p1.x, p1.y
            for j in self._adjacent_indices(int(x1 / cell_size), int(y1 / cell_size)):
                if j <= i:
                    continue
                p2 = particles[j]
                dx = x1 - p2.x
                dy = y1 - p2.y
                dist_sq = dx * dx + dy * dy
                if dist_sq < break_distance_sq:
                    neighbourhood[i].append((dist_sq, j))
                    neighbourhood[j].append((dist_sq, i))

        return neighbourhood

    def _update_connections(self, neighbourhood: list[list[tuple[float, int]]], count: int) -> None:
        """Выбирает пары частиц для связи и плавно меняет их непрозрачность."""
        active = self.active_connections
        stickiness = self.CONNECTION_STICKINESS
        max_connections = self.MAX_CONNECTIONS

        # Каждая частица «предлагает» связь ближайшим соседям.
        proposals: list[set[int]] = []
        for i in range(count):
            neighbours = neighbourhood[i]
            if not neighbours:
                proposals.append(set())
                continue

            ranked = sorted(
                (
                    # Уже существующая связь считается ближе, чем есть:
                    # без этого линии дрожали бы на границе радиуса.
                    (dist_sq * stickiness if (i, j) in active or (j, i) in active else dist_sq, j)
                    for dist_sq, j in neighbours
                ),
                key=_first,
            )
            proposals.append({j for _, j in ranked[:max_connections]})

        # Связь возникает только при взаимном предложении.
        ideal: dict[tuple[int, int], float] = {}
        for i in range(count):
            proposals_i = proposals[i]
            if not proposals_i:
                continue
            distances = {j: dist_sq for dist_sq, j in neighbourhood[i]}
            for j in proposals_i:
                if i < j and i in proposals[j]:
                    ideal[(i, j)] = distances[j]

        fade = self.FADE_SPEED
        updated: dict[tuple[int, int], tuple[float, float]] = {}

        for pair, dist_sq in ideal.items():
            opacity = active.get(pair, (0.0, 0.0))[0]
            updated[pair] = (min(1.0, opacity + fade), dist_sq)

        for pair, (opacity, dist_sq) in active.items():
            if pair not in ideal:
                faded = opacity - fade
                if faded > 0.0:
                    updated[pair] = (faded, dist_sq)

        self.active_connections = updated

    def _resolve_collisions(
        self, particles: list[Particle], neighbourhood: list[list[tuple[float, int]]]
    ) -> None:
        """Упруго разводит столкнувшиеся частицы."""
        for i, neighbours in enumerate(neighbourhood):
            p1 = particles[i]
            for _, j in neighbours:
                if j <= i:
                    continue
                p2 = particles[j]

                dx = p1.x - p2.x
                dy = p1.y - p2.y
                dist_sq = dx * dx + dy * dy
                min_dist = p1.size + p2.size

                if not (1e-9 < dist_sq < min_dist * min_dist):
                    continue

                dist = math.sqrt(dist_sq)
                nx, ny = dx / dist, dy / dist
                overlap = 0.5 * (min_dist - dist)

                p1.x += nx * overlap
                p1.y += ny * overlap
                p2.x -= nx * overlap
                p2.y -= ny * overlap

                # Разложение скоростей на нормаль и касательную к удару.
                tx, ty = -ny, nx
                v1n = p1.vx * nx + p1.vy * ny
                v1t = p1.vx * tx + p1.vy * ty
                v2n = p2.vx * nx + p2.vy * ny
                v2t = p2.vx * tx + p2.vy * ty

                m1, m2 = p1.mass, p2.mass
                total = m1 + m2
                v1n_new = (v1n * (m1 - m2) + 2.0 * m2 * v2n) / total
                v2n_new = (v2n * (m2 - m1) + 2.0 * m1 * v1n) / total

                p1.vx = v1n_new * nx + v1t * tx
                p1.vy = v1n_new * ny + v1t * ty
                p2.vx = v2n_new * nx + v2t * tx
                p2.vy = v2n_new * ny + v2t * ty

    def _bounce_off_walls(self, particles: list[Particle], w: int, h: int, r: float) -> None:
        """Отражает частицы от прямых стен и скруглённых углов."""
        rebound = self.WALL_REBOUND_FACTOR
        corners = self.corner_centers

        for particle in particles:
            x, y, size = particle.x, particle.y, particle.size

            if x - size < 0 and r <= y <= h - r:
                particle.x = size
                if particle.vx < 0:
                    particle.vx = -particle.vx * rebound
            elif x + size > w and r <= y <= h - r:
                particle.x = w - size
                if particle.vx > 0:
                    particle.vx = -particle.vx * rebound

            if y - size < 0 and r <= x <= w - r:
                particle.y = size
                if particle.vy < 0:
                    particle.vy = -particle.vy * rebound
            elif y + size > h and r <= x <= w - r:
                particle.y = h - size
                if particle.vy > 0:
                    particle.vy = -particle.vy * rebound

            if not corners:
                continue

            corner = None
            if x < r and y < r:
                corner = corners[0]
            elif x > w - r and y < r:
                corner = corners[1]
            elif x > w - r and y > h - r:
                corner = corners[2]
            elif x < r and y > h - r:
                corner = corners[3]

            if corner is None:
                continue

            dx = particle.x - corner[0]
            dy = particle.y - corner[1]
            dist = math.sqrt(dx * dx + dy * dy)
            if dist > r - size and dist > 1e-6:
                nx, ny = dx / dist, dy / dist
                vel_dot_normal = particle.vx * nx + particle.vy * ny
                if vel_dot_normal > 0:
                    factor = (1.0 + rebound) * vel_dot_normal
                    particle.vx -= factor * nx
                    particle.vy -= factor * ny
                particle.x = corner[0] + nx * (r - size)
                particle.y = corner[1] + ny * (r - size)

    def _clamp_speeds(self, particles: list[Particle]) -> None:
        """Удерживает скорость частиц в заданном диапазоне."""
        min_sq, max_sq = self.min_speed_sq, self.max_speed_sq
        min_speed, max_speed = self.MIN_SPEED, self.MAX_SPEED

        for particle in particles:
            speed_sq = particle.vx * particle.vx + particle.vy * particle.vy

            if speed_sq < min_sq:
                if speed_sq < 1e-9:
                    angle = random.uniform(0, 2 * math.pi)
                    particle.vx = min_speed * math.cos(angle)
                    particle.vy = min_speed * math.sin(angle)
                else:
                    scale = min_speed / math.sqrt(speed_sq)
                    particle.vx *= scale
                    particle.vy *= scale
            elif speed_sq > max_sq:
                scale = max_speed / math.sqrt(speed_sq)
                particle.vx *= scale
                particle.vy *= scale

    # --- Управление анимацией --------------------------------------------

    def start_animation(self) -> None:
        """Запускает анимацию."""
        if not self.is_animation_running:
            self.is_animation_running = True
            self.time_counter = 0.0
            self.animation_timer.start(self.FRAME_INTERVAL_MS)

    def stop_animation(self) -> None:
        """Останавливает анимацию и освобождает процессор."""
        if self.is_animation_running:
            self.is_animation_running = False
            self.animation_timer.stop()

    # --- Отрисовка --------------------------------------------------------

    def paintEvent(self, event: QPaintEvent) -> None:
        """Рисует фон, линии связей и частицы."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        clip = QPainterPath()
        clip.addRoundedRect(QRectF(self.rect()), self.CORNER_RADIUS, self.CORNER_RADIUS)
        painter.setClipPath(clip)
        painter.fillRect(self.rect(), self.BG_COLOR)

        particles = self.particles
        if not particles:
            return

        self._paint_connections(painter, particles)
        self._paint_particles(painter, particles)

    def _paint_connections(self, painter: QPainter, particles: list[Particle]) -> None:
        connection_distance_sq = self.connection_distance_sq
        time_counter = self.time_counter
        line_colors = self._line_colors
        pen = QPen(self.LINE_BASE_COLOR, 1)

        for (i, j), (opacity, dist_sq) in self.active_connections.items():
            if opacity <= 0.0:
                continue

            p1, p2 = particles[i], particles[j]
            base_alpha = 90.0 * (1.0 - dist_sq / connection_distance_sq)
            alpha = int(base_alpha * opacity)
            if alpha <= 0:
                continue

            color = QColor(line_colors[self._hue_index(math.sin(time_counter + p1.x * 0.01))])
            color.setAlpha(alpha)
            pen.setColor(color)
            painter.setPen(pen)
            painter.drawLine(QPointF(p1.x, p1.y), QPointF(p2.x, p2.y))

    def _paint_particles(self, painter: QPainter, particles: list[Particle]) -> None:
        painter.setPen(Qt.PenStyle.NoPen)
        half_time = self.time_counter * 0.5
        particle_colors = self._particle_colors

        for particle in particles:
            index = self._hue_index(math.sin(half_time + particle.y * 0.01))
            painter.setBrush(particle_colors[index])
            painter.drawEllipse(QPointF(particle.x, particle.y), particle.size, particle.size)

    # --- События ----------------------------------------------------------

    def resizeEvent(self, event: QResizeEvent) -> None:
        """Пересоздаёт частицы под новый размер виджета."""
        self.init_particles()

    def showEvent(self, event: QShowEvent) -> None:
        """Возобновляет анимацию после разворачивания окна."""
        super().showEvent(event)
        self.init_particles()
        if self.is_animation_running and not self.animation_timer.isActive():
            self.animation_timer.start(self.FRAME_INTERVAL_MS)

    def hideEvent(self, event: QShowEvent) -> None:
        """Останавливает таймер, пока виджет не виден."""
        super().hideEvent(event)
        self.animation_timer.stop()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        """Запоминает позицию курсора для отталкивания частиц."""
        position = event.position()
        self.mouse_x = position.x()
        self.mouse_y = position.y()

    def leaveEvent(self, event: QMouseEvent) -> None:
        """Убирает влияние курсора, когда он покидает виджет."""
        self.mouse_x = -1.0
        self.mouse_y = -1.0

    def update_mouse_position(self, pos: QPoint) -> None:
        """Позволяет обновить позицию курсора извне (события родителя)."""
        self.mouse_x = float(pos.x())
        self.mouse_y = float(pos.y())


def _first(item: tuple[float, int]) -> float:
    """Ключ сортировки по расстоянию."""
    return item[0]
