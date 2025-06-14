# src/winspector/gui/icon_generator.py
"""
Модуль для программной генерации и кеширования иконок.

Это позволяет избежать хранения файлов иконок в ресурсах, делая
приложение более самодостаточным и упрощая изменение стиля иконок.
"""
import logging
from typing import Dict

from PyQt6.QtGui import QIcon, QPixmap, QPainter, QColor, QPen, QPainterPath
from PyQt6.QtCore import Qt, QRect

logger = logging.getLogger(__name__)


class IconGenerator:
    """
    Генерирует и кеширует QIcon на лету, используя QPainter.
    """
    # Словарь для кеширования уже сгенерированных иконок
    # Ключ: кортеж (имя_иконки, размер), Значение: QIcon
    _icon_cache: Dict[tuple[str, int], QIcon] = {}

    # Централизованная палитра для всех иконок
    PALETTE = {
        "primary": QColor("#FFFFFF"),
        "accent": QColor("#00AEEF"),
        "stroke_width": 2,
    }

    @classmethod
    def get_icon(cls, name: str, size: int = 32) -> QIcon:
        """
        Возвращает иконку по имени, используя кеш.

        Args:
            name (str): Имя иконки (например, "scan", "optimize").
            size (int): Размер иконки в пикселях.

        Returns:
            Сгенерированная или кешированная QIcon.
        """
        cache_key = (name, size)
        if cache_key in cls._icon_cache:
            return cls._icon_cache[cache_key]

        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        # Делегирование рисовки на основе имени
        draw_func_name = f"_draw_{name}"
        draw_func = getattr(cls, draw_func_name, None)

        if draw_func is None:
            logger.warning(f"Запрошена неизвестная иконка '{name}'. Будет использована иконка по умолчанию.")
            draw_func = cls._draw_default
        
        draw_func(painter, size, cls.PALETTE)
        
        painter.end()
        
        icon = QIcon(pixmap)
        cls._icon_cache[cache_key] = icon
        return icon

    @staticmethod
    def _setup_pen(palette: Dict) -> QPen:
        """Вспомогательный метод для настройки пера."""
        pen = QPen(palette["primary"])
        pen.setWidth(palette["stroke_width"])
        return pen

    @staticmethod
    def _draw_scan(painter: QPainter, size: int, palette: Dict) -> None:
        """Рисует иконку "Сканирование" (лупа)."""
        painter.setPen(IconGenerator._setup_pen(palette))
        
        center_x, center_y = size // 2, size // 2
        radius = size // 4
        handle_length = size // 4
        
        painter.drawEllipse(center_x - radius, center_y - radius, radius * 2, radius * 2)
        painter.drawLine(
            int(center_x + radius * 0.707), int(center_y + radius * 0.707),
            int(center_x + (radius + handle_length) * 0.707), int(center_y + (radius + handle_length) * 0.707)
        )

    @staticmethod
    def _draw_optimize(painter: QPainter, size: int, palette: Dict) -> None:
        """Рисует иконку "Оптимизация" (ракета)."""
        painter.setPen(IconGenerator._setup_pen(palette))
        
        s = size
        rect = QRect(s // 4, s // 8, s // 2, s * 3 // 4)
        painter.drawRoundedRect(rect, 5, 5)
        # Наконечник
        painter.drawLine(s // 2, s // 8, s // 2, 0)
        # Стабилизаторы
        painter.drawLine(s // 4, s * 6 // 8, s // 8, s)
        painter.drawLine(s * 3 // 4, s * 6 // 8, s * 7 // 8, s)

    @staticmethod
    def _draw_health(painter: QPainter, size: int, palette: Dict) -> None:
        """Рисует иконку "Здоровье" (сердце со щитом)."""
        pen = IconGenerator._setup_pen(palette)
        painter.setPen(pen)
        
        s = float(size)
        # Рисуем контур щита
        path = QPainterPath()
        path.moveTo(s * 0.5, s * 0.1)
        path.cubicTo(s * 0.9, s * 0.2, s * 0.9, s * 0.8, s * 0.5, s * 0.9)
        path.cubicTo(s * 0.1, s * 0.8, s * 0.1, s * 0.2, s * 0.5, s * 0.1)
        painter.drawPath(path)
        
        # Рисуем плюсик внутри
        pen.setColor(palette["accent"])
        painter.setPen(pen)
        painter.drawLine(int(s*0.35), int(s*0.5), int(s*0.65), int(s*0.5))
        painter.drawLine(int(s*0.5), int(s*0.35), int(s*0.5), int(s*0.65))

    @staticmethod
    def _draw_ai_explain(painter: QPainter, size: int, palette: Dict) -> None:
        """Рисует иконку "AI Объяснение" (мозг)."""
        painter.setPen(IconGenerator._setup_pen(palette))
        
        center_x, center_y = size // 2, size // 2
        # Верхняя часть
        painter.drawArc(size // 4, size // 4, size // 2, size // 2, 0, 180 * 16)
        # Левая извилина
        painter.drawArc(size // 4, center_y - size // 8, size // 4, size // 4, 180 * 16, 180 * 16)
        # Правая извилина
        painter.drawArc(center_x, center_y - size // 8, size // 4, size // 4, 180 * 16, 180 * 16)
        # "Ствол"
        painter.drawLine(center_x, center_y, center_x, center_y + size // 4)

    @staticmethod
    def _draw_default(painter: QPainter, size: int, palette: Dict) -> None:
        """Рисует иконку по умолчанию (вопросительный знак в круге)."""
        painter.setPen(IconGenerator._setup_pen(palette))
        
        painter.drawEllipse(size // 8, size // 8, size * 3 // 4, size * 3 // 4)
        font = painter.font()
        font.setPixelSize(size // 2)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(QRect(0, 0, size, size), Qt.AlignmentFlag.AlignCenter, "?")