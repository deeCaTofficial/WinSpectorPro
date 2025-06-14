# src/winspector/gui/widgets/neural_background.py
"""
Виджет для отображения анимированного фона в виде "нейронной сети".
Создает эффект движущихся частиц, соединенных линиями, с параллаксом от мыши.
"""
import random
from typing import List, Dict, Any, Optional

from PyQt6.QtWidgets import QWidget
from PyQt6.QtGui import (
    QPainter, QColor, QPen, QBrush,
    QPaintEvent, QResizeEvent, QShowEvent, QMouseEvent
)
from PyQt6.QtCore import QTimer, QPointF, Qt, QRect


class NeuralBackgroundWidget(QWidget):
    """
    Анимированный фон с эффектом параллакса от движения мыши.
    """
    
    # --- Настройки для легкой кастомизации ---
    PARTICLE_COUNT: int = 50
    CONNECTION_DISTANCE: float = 120.0
    BASE_SPEED: float = 0.5
    
    # Цвета
    BG_COLOR = QColor(33, 37, 43)
    PARTICLE_COLOR = QColor(82, 152, 215, 150)
    LINE_BASE_COLOR = QColor(82, 152, 215)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        
        self.particles: List[Dict[str, Any]] = []
        # Инициализируем позицию мыши в центре, чтобы избежать резкого скачка при запуске
        self.mouse_pos = QPointF(-1, -1) 

        # Таймер для анимации
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_particles)
        self.timer.start(33)  # ~30 FPS

        # Включаем отслеживание мыши для эффекта параллакса
        self.setMouseTracking(True)

    def _create_particle(self) -> Dict[str, Any]:
        """Создает одну частицу со случайными параметрами."""
        return {
            "pos": QPointF(random.uniform(0, self.width()), random.uniform(0, self.height())),
            "vel": QPointF(random.uniform(-self.BASE_SPEED, self.BASE_SPEED), 
                           random.uniform(-self.BASE_SPEED, self.BASE_SPEED)),
            "size": random.uniform(2, 4.5),
            # Коэффициент для параллакса (глубокие частицы движутся медленнее)
            "parallax_factor": random.uniform(0.1, 0.5)
        }

    def init_particles(self) -> None:
        """Инициализирует или пересоздает все частицы."""
        if not self.isVisible() or self.width() == 0 or self.height() == 0:
            return # Не создавать частицы, если виджет не виден или не имеет размера
            
        self.particles = [self._create_particle() for _ in range(self.PARTICLE_COUNT)]
        
        # Если мышь еще не двигалась, устанавливаем ее в центр
        if self.mouse_pos.x() < 0:
            self.mouse_pos = QPointF(self.width() / 2, self.height() / 2)
            
        self.update() # Запросить перерисовку

    def update_particles(self) -> None:
        """Обновляет позицию каждой частицы на каждом кадре."""
        for p in self.particles:
            # Движение от собственной скорости
            p["pos"] += p["vel"]
            
            # Движение от мыши (параллакс)
            # Частица медленно движется к курсору
            mouse_influence = (self.mouse_pos - p["pos"]) * 0.0005 * p["parallax_factor"]
            p["pos"] += mouse_influence

            # Логика отскока от стен
            if p["pos"].x() < 0:
                p["pos"].setX(0)
                p["vel"].setX(abs(p["vel"].x()))
            elif p["pos"].x() > self.width():
                p["pos"].setX(self.width())
                p["vel"].setX(-abs(p["vel"].x()))
            
            if p["pos"].y() < 0:
                p["pos"].setY(0)
                p["vel"].setY(abs(p["vel"].y()))
            elif p["pos"].y() > self.height():
                p["pos"].setY(self.height())
                p["vel"].setY(-abs(p["vel"].y()))
                
        self.update() # Запрашиваем перерисовку виджета

    def paintEvent(self, event: QPaintEvent) -> None:
        """Отрисовывает фон, частицы и линии между ними. Оптимизирован."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        # 1. Рисуем фон
        painter.fillRect(self.rect(), self.BG_COLOR)

        if not self.particles:
            return

        # 2. Рисуем линии между близкими частицами
        line_pen = QPen(self.LINE_BASE_COLOR, 1)
        
        for i in range(self.PARTICLE_COUNT):
            for j in range(i + 1, self.PARTICLE_COUNT):
                p1 = self.particles[i]["pos"]
                p2 = self.particles[j]["pos"]
                
                # Используем квадрат расстояния, чтобы избежать дорогостоящего sqrt()
                dist_sq = (p1.x() - p2.x())**2 + (p1.y() - p2.y())**2
                
                if dist_sq < self.CONNECTION_DISTANCE**2:
                    # Рассчитываем альфа-канал на основе расстояния
                    alpha = int(70 * (1 - dist_sq / self.CONNECTION_DISTANCE**2))
                    
                    line_pen.setColor(QColor(
                        self.LINE_BASE_COLOR.red(),
                        self.LINE_BASE_COLOR.green(),
                        self.LINE_BASE_COLOR.blue(),
                        alpha
                    ))
                    painter.setPen(line_pen)
                    painter.drawLine(p1, p2)
        
        # 3. Рисуем сами частицы
        painter.setBrush(QBrush(self.PARTICLE_COLOR))
        painter.setPen(Qt.PenStyle.NoPen)
        for p in self.particles:
            painter.drawEllipse(p["pos"], p["size"], p["size"])
            
    # --- Обработчики событий ---

    def resizeEvent(self, event: QResizeEvent) -> None:
        """Вызывается при изменении размера окна для пересоздания частиц."""
        super().resizeEvent(event)
        self.init_particles()
        
    def showEvent(self, event: QShowEvent) -> None:
        """Вызывается при первом показе виджета."""
        super().showEvent(event)
        self.init_particles()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        """Отслеживает позицию мыши для эффекта параллакса."""
        super().mouseMoveEvent(event)
        self.mouse_pos = QPointF(event.position())