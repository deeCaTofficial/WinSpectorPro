# src/winspector/gui/widgets/neural_background.py
"""
Виджет для отображения анимированного фона в виде "нейронной сети".
Создает эффект движущихся частиц, соединенных линиями, с параллаксом от мыши.
"""
import math
import random
import sys
from typing import List, Dict, Any, Optional

from PyQt6.QtWidgets import QWidget, QPushButton, QApplication, QMainWindow
from PyQt6.QtGui import (
    QPainter, QColor, QPen, QBrush, QPaintEvent, QResizeEvent, QShowEvent,
    QMouseEvent, QPainterPath
)
from PyQt6.QtCore import QTimer, QPointF, Qt, QRect, QRectF, pyqtSignal, QObject, QPropertyAnimation, QEasingCurve, QSequentialAnimationGroup, QPoint


class Particle:
    """Легковесный класс для хранения данных о частице."""
    __slots__ = ('pos', 'vel', 'size', 'parallax_factor', 'mass')
    
    def __init__(self, pos: QPointF, vel: QPointF, size: float, parallax_factor: float, mass: float):
        self.pos = pos
        self.vel = vel
        self.size = size
        self.parallax_factor = parallax_factor
        self.mass = mass


class NeuralBackgroundWidget(QWidget):
    """Анимированный фон с эффектом параллакса от движения мыши."""
    
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

    BG_COLOR = QColor(33, 37, 43)
    PARTICLE_COLOR = QColor(82, 152, 215, 150)
    LINE_BASE_COLOR = QColor(82, 152, 215)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        
        self.particles: List[Particle] = []
        self.mouse_pos = QPointF(-1, -1) 

        self.time_counter = 0

        # Отслеживание активных и затухающих соединений.
        # Храним (opacity, dist_sq) для оптимизации.
        self.active_connections: Dict[tuple[int, int], tuple[float, float]] = {}

        # Оптимизация: пространственная сетка для ускорения поиска соседей
        self.grid: Dict[tuple[int, int], List[int]] = {}
        self.grid_cell_size: float = self.CONNECTION_DISTANCE
        
        # Предварительно рассчитанные значения для оптимизации
        self.connection_distance_sq: float = self.CONNECTION_DISTANCE**2
        self.break_distance_sq: float = self.connection_distance_sq * (self.CONNECTION_BREAK_FACTOR**2)
        self.min_speed_sq: float = self.MIN_SPEED**2
        self.max_speed_sq: float = self.MAX_SPEED**2
        self.corner_centers: List[QPointF] = []

        self.animation_timer = QTimer(self)
        self.animation_timer.timeout.connect(self.update_particles)
        self.animation_timer.start(45)  # ~33 FPS

        self.setMouseTracking(True)

        self.is_animation_running = False

    def _rand_float(self, min_val: float = 0.0, max_val: float = 1.0) -> float:
        """Генерирует случайное число с плавающей точкой."""
        return random.uniform(min_val, max_val)

    def _create_particle(self) -> Particle:
        """Создает одну частицу со случайными параметрами."""
        size = random.uniform(2, 4.5)
        return Particle(
            pos=QPointF(
                self.width() * (0.05 + 0.9 * self._rand_float()),
                self.height() * (0.05 + 0.9 * self._rand_float())
            ),
            vel=QPointF(
                self._rand_float(-self.BASE_SPEED, self.BASE_SPEED),
                self._rand_float(-self.BASE_SPEED, self.BASE_SPEED)
            ),
            size=size,
            parallax_factor=random.uniform(0.1, 0.5),
            mass=size*size
        )

    def _create_spatial_grid(self) -> None:
        """
        Перестраивает пространственную сетку для быстрой проверки столкновений.

        Этот метод вызывается в каждом кадре, чтобы обновить положение частиц
        в сетке, что позволяет избежать O(n^2) проверок, ограничивая их
        только соседними ячейками.
        """
        self.grid.clear()
        if not self.grid_cell_size or not self.particles:
            return
            
        for i, p in enumerate(self.particles):
            cell_x = int(p.pos.x() / self.grid_cell_size)
            cell_y = int(p.pos.y() / self.grid_cell_size)
            
            if (cell_x, cell_y) not in self.grid:
                self.grid[(cell_x, cell_y)] = []
            self.grid[(cell_x, cell_y)].append(i)

    def _get_adjacent_indices(self, cell_x: int, cell_y: int) -> List[int]:
        """
        Возвращает список индексов частиц из указанной ячейки и 8 соседних.

        Args:
            cell_x: Координата X ячейки.
            cell_y: Координата Y ячейки.

        Returns:
            Список индексов частиц, находящихся в соседних ячейках.
        """
        indices = []
        for dx in range(-1, 2):
            for dy in range(-1, 2):
                neighbor_cell = (cell_x + dx, cell_y + dy)
                if neighbor_cell in self.grid:
                    indices.extend(self.grid[neighbor_cell])
        return indices

    def init_particles(self) -> None:
        """
        Инициализирует или пересоздает все частицы и сбрасывает активные соединения.
        """
        if not self.isVisible() or self.width() == 0 or self.height() == 0:
            return
        
        w = self.width()
        h = self.height()
        r = self.CORNER_RADIUS
        self.corner_centers = [
            QPointF(r, r), QPointF(w - r, r),
            QPointF(w - r, h - r), QPointF(r, h - r)
        ]
        
        self.particles = [self._create_particle() for _ in range(self.PARTICLE_COUNT)]
        # Сбрасываем все существующие соединения, чтобы избежать "призрачных" линий
        # при пересоздании частиц (например, после разворачивания окна).
        self.active_connections.clear()
        
        if self.mouse_pos.x() < 0:
            self.mouse_pos = QPointF(self.width() / 2, self.height() / 2)
            
        self.update()

    def update_particles(self) -> None:
        """
        Основной метод, обновляющий состояние всех частиц в каждом кадре.
        
        Выполняется в несколько этапов:
        1. Обновление позиций частиц и отталкивание от курсора.
        2. Определение "идеальных" соединений с помощью стабильного алгоритма.
        3. Обработка упругих столкновений частиц друг с другом.
        4. Обработка столкновений со стенами и углами.
        5. Ограничение скорости частиц в заданном диапазоне.
        """
        if not self.is_animation_running:
            return

        self.time_counter += 0.03
        
        # --- Кэширование атрибутов для оптимизации ---
        particles = self.particles
        if not particles:
            return
            
        particle_count = len(particles)
        w, h, r = self.width(), self.height(), self.CORNER_RADIUS
        
        # --- Этап 1: Обновление позиций и отталкивание от мыши ---
        self._create_spatial_grid()
        
        mouse_pos = self.mouse_pos
        repulsion_radius_sq = self.REPULSION_RADIUS**2
        repulsion_strength = self.REPULSION_STRENGTH
        
        for p in particles:
            p.pos += p.vel
            if mouse_pos.x() > 0:
                vec_from_mouse = p.pos - mouse_pos
                dist_sq = vec_from_mouse.x()**2 + vec_from_mouse.y()**2
                if dist_sq < repulsion_radius_sq and dist_sq > 1e-6:
                    dist = math.sqrt(dist_sq)
                    repulsion_force = (1 - dist / self.REPULSION_RADIUS) * repulsion_strength
                    p.pos += (vec_from_mouse / dist) * repulsion_force

        # --- Этап 2: Стабильный алгоритм определения соединений ---
        
        # 2.1. Для каждой частицы находим всех соседей в радиусе
        particle_neighbors = [[] for _ in range(particle_count)]
        break_distance_sq = self.break_distance_sq

        for i in range(particle_count):
            pos1 = particles[i].pos
            cell_x = int(pos1.x() / self.grid_cell_size)
            cell_y = int(pos1.y() / self.grid_cell_size)
            for j_idx in self._get_adjacent_indices(cell_x, cell_y):
                if i >= j_idx: continue
                
                pos2 = particles[j_idx].pos
                dist_sq = (pos1.x() - pos2.x())**2 + (pos1.y() - pos2.y())**2
                
                if dist_sq < break_distance_sq:
                    particle_neighbors[i].append((dist_sq, j_idx))
                    particle_neighbors[j_idx].append((dist_sq, i))

        # 2.2. Каждая частица выбирает, кому "предложить" дружбу
        active_connections = self.active_connections
        proposals = [set() for _ in range(particle_count)]
        stickiness_factor = self.CONNECTION_STICKINESS
        max_connections = self.MAX_CONNECTIONS

        for i in range(particle_count):
            # Создаем временную копию для безопасной сортировки с "эффективным" расстоянием
            sortable_neighbors = []
            for dist_sq, j_idx in particle_neighbors[i]:
                effective_dist_sq = dist_sq
                # Применяем "липкость", если связь уже активна
                if tuple(sorted((i, j_idx))) in active_connections:
                    effective_dist_sq *= stickiness_factor
                sortable_neighbors.append((effective_dist_sq, j_idx))
            
            # Сортируем по эффективной дистанции
            sortable_neighbors.sort(key=lambda x: x[0])
            
            # Делаем предложения лучшим N кандидатам
            for _, j_idx in sortable_neighbors[:max_connections]:
                proposals[i].add(j_idx)

        # 2.3. Находим взаимные пары ("двойное рукопожатие")
        ideal_connections = {}
        for i in range(particle_count):
            neighbor_dist_map = {j: d for d, j in particle_neighbors[i]}
            for j in proposals[i]:
                # Проверяем пару только один раз (i < j) и проверяем взаимность
                if i < j and i in proposals[j]:
                    ideal_connections[tuple(sorted((i, j)))] = neighbor_dist_map[j]
        
        # 2.4. Обновляем непрозрачность (плавное появление/исчезновение)
        next_active_connections = {}
        fade_speed = self.FADE_SPEED

        for pair, dist_sq in ideal_connections.items():
            current_opacity, _ = active_connections.get(pair, (0.0, 0.0))
            new_opacity = min(1.0, current_opacity + fade_speed)
            next_active_connections[pair] = (new_opacity, dist_sq)

        for pair, (current_opacity, old_dist_sq) in active_connections.items():
            if pair not in ideal_connections:
                new_opacity = max(0.0, current_opacity - fade_speed)
                if new_opacity > 0:
                    next_active_connections[pair] = (new_opacity, old_dist_sq)
            
        self.active_connections = next_active_connections

        # --- Этап 3: Обработка столкновений частиц ---
        for i in range(particle_count):
            p1 = particles[i]
            
            cell_x = int(p1.pos.x() / self.grid_cell_size)
            cell_y = int(p1.pos.y() / self.grid_cell_size)
            adjacent_indices = self._get_adjacent_indices(cell_x, cell_y)

            for j_idx in adjacent_indices:
                if j_idx <= i: continue
                
                p2 = particles[j_idx]
                vec_diff = p1.pos - p2.pos
                dist_sq = vec_diff.x()**2 + vec_diff.y()**2
                min_dist = p1.size + p2.size

                if dist_sq < min_dist**2 and dist_sq > 1e-9:
                    dist = math.sqrt(dist_sq)
                    
                    overlap = 0.5 * (min_dist - dist)
                    vec_diff_norm = vec_diff / dist
                    p1.pos += vec_diff_norm * overlap
                    p2.pos -= vec_diff_norm * overlap

                    normal = vec_diff_norm
                    tangent = QPointF(-normal.y(), normal.x())

                    v1n = QPointF.dotProduct(p1.vel, normal)
                    v1t = QPointF.dotProduct(p1.vel, tangent)
                    v2n = QPointF.dotProduct(p2.vel, normal)
                    v2t = QPointF.dotProduct(p2.vel, tangent)

                    m1, m2 = p1.mass, p2.mass
                    v1n_new = (v1n * (m1 - m2) + 2 * m2 * v2n) / (m1 + m2)
                    v2n_new = (v2n * (m2 - m1) + 2 * m1 * v1n) / (m1 + m2)

                    p1.vel = v1n_new * normal + v1t * tangent
                    p2.vel = v2n_new * normal + v2t * tangent

        # --- Этап 4: Обработка столкновений со стенами ---
        wall_rebound_factor = self.WALL_REBOUND_FACTOR
        corner_centers = self.corner_centers
        for p in particles:
            px, py = p.pos.x(), p.pos.y()
            ps = p.size

            # Столкновения с границами
            if px - ps < 0 and r <= py <= h - r:
                p.pos.setX(ps)
                if p.vel.x() < 0: p.vel.setX(-p.vel.x() * wall_rebound_factor)
            elif px + ps > w and r <= py <= h - r:
                p.pos.setX(w - ps)
                if p.vel.x() > 0: p.vel.setX(-p.vel.x() * wall_rebound_factor)
            if py - ps < 0 and r <= px <= w - r:
                p.pos.setY(ps)
                if p.vel.y() < 0: p.vel.setY(-p.vel.y() * wall_rebound_factor)
            elif py + ps > h and r <= px <= w - r:
                p.pos.setY(h - ps)
                if p.vel.y() > 0: p.vel.setY(-p.vel.y() * wall_rebound_factor)

            # Углы
            center = None
            if px < r and py < r: center = corner_centers[0]
            elif px > w - r and py < r: center = corner_centers[1]
            elif px > w - r and py > h - r: center = corner_centers[2]
            elif px < r and py > h - r: center = corner_centers[3]

            if center is not None:
                vec_to_center = p.pos - center
                dist = math.sqrt(vec_to_center.x()**2 + vec_to_center.y()**2)
                
                if dist > r - ps and dist > 1e-6:
                    normal = vec_to_center / dist
                    vel_dot_normal = QPointF.dotProduct(p.vel, normal)
                    if vel_dot_normal > 0:
                        p.vel = p.vel - (1 + wall_rebound_factor) * vel_dot_normal * normal
                    p.pos = center + normal * (r - ps)

        # --- Этап 5: Ограничение скорости ---
        min_sq, max_sq = self.min_speed_sq, self.max_speed_sq
        min_speed = self.MIN_SPEED
        
        for p in particles:
            vel = p.vel
            speed_sq = vel.x()**2 + vel.y()**2
            
            if speed_sq < min_sq:
                if speed_sq < 1e-9:
                    angle = random.uniform(0, 2 * math.pi)
                    vel.setX(min_speed * math.cos(angle))
                    vel.setY(min_speed * math.sin(angle))
                else:
                    scale = min_speed / math.sqrt(speed_sq)
                    p.vel *= scale
            elif speed_sq > max_sq:
                scale = self.MAX_SPEED / math.sqrt(speed_sq)
                p.vel *= scale

        self.update()

    def start_animation(self):
        """Запускает таймер анимации, если он еще не запущен."""
        if not self.is_animation_running:
            self.is_animation_running = True
            self.time_counter = 0 
            self.animation_timer.start(30)

    def stop_animation(self):
        """Останавливает таймер анимации."""
        if self.is_animation_running:
            self.is_animation_running = False
            if self.animation_timer.isActive():
                self.animation_timer.stop()

    def paintEvent(self, event: QPaintEvent) -> None:
        """Отрисовывает фон, частицы и переливающиеся в синей гамме линии."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rounded_rect_path = QPainterPath()
        rounded_rect_path.addRoundedRect(QRectF(self.rect()), self.CORNER_RADIUS, self.CORNER_RADIUS)

        painter.setClipPath(rounded_rect_path)
        
        painter.fillRect(self.rect(), self.BG_COLOR)

        # Кэшируем атрибуты для оптимизации
        particles = self.particles
        if not particles:
            return

        active_connections = self.active_connections
        connection_distance_sq = self.connection_distance_sq
        time_counter = self.time_counter
        line_pen = QPen(self.LINE_BASE_COLOR, 1)

        for (i, j), (opacity, dist_sq) in active_connections.items():
            if opacity > 0:
                pos1 = particles[i].pos
                pos2 = particles[j].pos

                # Базовая альфа зависит от расстояния
                base_alpha = 90 * (1 - dist_sq / connection_distance_sq)
                # Итоговая альфа учитывает плавное появление/исчезновение
                final_alpha = int(base_alpha * opacity)

                if final_alpha > 0:
                    oscillation = math.sin(time_counter + pos1.x() * 0.01)

                    base_hue = 0.62
                    hue_range = 0.08
                    hue_value = base_hue + (oscillation * hue_range)

                    current_color = QColor.fromHslF(hue_value, 0.8, 0.6, final_alpha / 255.0)
                    
                    line_pen.setColor(current_color)
                    painter.setPen(line_pen)
                    painter.drawLine(pos1, pos2)
        
        painter.setPen(Qt.PenStyle.NoPen)
        for p in particles:
            oscillation = math.sin(time_counter * 0.5 + p.pos.y() * 0.01)
            base_hue = 0.62
            hue_range = 0.08
            particle_hue = base_hue + (oscillation * hue_range)
            
            particle_color = QColor.fromHslF(particle_hue, 0.9, 0.7, 0.8)
            painter.setBrush(particle_color)
            painter.drawEllipse(p.pos, p.size, p.size)
            
    def resizeEvent(self, event: QResizeEvent) -> None:
        """Пересоздает частицы при изменении размера виджета."""
        self.init_particles()

    def showEvent(self, event: QShowEvent) -> None:
        """
        Инициализирует частицы и перезапускает анимацию, когда виджет становится видимым.
        Это решает проблему "замерзания" после сворачивания и разворачивания окна.
        """
        super().showEvent(event)
        self.init_particles()
        # Если анимация должна была работать, но таймер был остановлен (например,
        # через hideEvent), мы его перезапускаем.
        if self.is_animation_running and not self.animation_timer.isActive():
            self.animation_timer.start(30)

    def hideEvent(self, event: QShowEvent) -> None:
        """
        Приостанавливает анимацию при скрытии виджета для экономии ресурсов.
        """
        super().hideEvent(event)
        if self.animation_timer.isActive():
            self.animation_timer.stop()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        """Обновляет позицию курсора для эффекта параллакса."""
        self.mouse_pos = event.position()

    def leaveEvent(self, event: QMouseEvent) -> None:
        """Обновляет позицию курсора при выходе из виджета."""
        self.mouse_pos = QPointF(-1, -1)

    def update_mouse_position(self, pos: QPoint):
        """Публичный метод для обновления позиции мыши извне."""
        # Конвертируем QPoint в QPointF для совместимости с физикой частиц
        self.mouse_pos = QPointF(pos)
        # Не вызываем update() здесь, чтобы не перегружать рендер,
        # так как он и так вызывается по таймеру.

    def _update_animation(self):
        if not self.is_visible or not self.particles:
            return


class PulsingButton(QPushButton):
    def __init__(self, text, parent=None):
        super().__init__(text, parent)
        self._animation = QPropertyAnimation(self, b"styleSheet")
        self._animation.setDuration(1500)
        
        self._animation.setLoopCount(-1) # Loop indefinitely
        self.setStyleSheet("""
            PulsingButton {
                background-color: #415a77;
                color: #e0e1dd;
                border: 2px solid #778da9;
                padding: 10px;
                border-radius: 5px;
            }
        """)

    def start_animation(self):
        self._animation.setKeyValues([
            (0.0, "background-color: #415a77; border: 2px solid #778da9;"),
            (0.5, "background-color: #5a7a9f; border: 2px solid #9fb8d5;"),
            (1.0, "background-color: #415a77; border: 2px solid #778da9;"),
        ])
        self._animation.start()

    def stop_animation(self):
        self._animation.stop()


if __name__ == '__main__':
    app = QApplication(sys.argv)

    WIN_WIDTH = 800
    WIN_HEIGHT = 600

    window = QMainWindow()
    window.setWindowTitle("NeuralBackgroundWidget Test")
    window.setFixedSize(WIN_WIDTH, WIN_HEIGHT)

    neural_background = NeuralBackgroundWidget()
    window.setCentralWidget(neural_background)

    button = PulsingButton("Test Button", parent=neural_background)
    BUTTON_WIDTH = 200
    BUTTON_HEIGHT = 50
    button.resize(BUTTON_WIDTH, BUTTON_HEIGHT)
    button.move((WIN_WIDTH - BUTTON_WIDTH) // 2, (WIN_HEIGHT - BUTTON_HEIGHT) // 2)

    neural_background.start_animation()
    button.start_animation()

    window.show()

    sys.exit(app.exec())