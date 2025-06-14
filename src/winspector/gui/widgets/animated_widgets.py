# src/winspector/gui/widgets/animated_widgets.py
"""
Кастомные анимированные виджеты для создания современного
и динамичного пользовательского интерфейса WinSpector Pro.
"""
from PyQt6.QtWidgets import QWidget, QLabel, QVBoxLayout, QGraphicsOpacityEffect
from PyQt6.QtGui import (
    QIcon, QPainter, QColor, QBrush, QPen, QFont, QRadialGradient,
    QPixmap, QPainterPath
)
from PyQt6.QtCore import (
    QPropertyAnimation, pyqtProperty, QEasingCurve, QTimer, QPointF, Qt,
    QSize, pyqtSignal, QRectF
)
from typing import Optional, Union

# ==============================================================================
#  AnimatedCounterLabel
# ==============================================================================

class AnimatedCounterLabel(QLabel):
    """
    QLabel, который анимированно "считает" от одного числового
    значения до другого.
    """
    def __init__(self, text_format: str = "{value}", parent: Optional[QWidget] = None):
        super().__init__("0", parent)
        self._value: float = 0.0
        self.text_format = text_format
        
        self.animation = QPropertyAnimation(self, b"value")
        self.animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.setText(self.text_format.format(value=0))

    @pyqtProperty(float)
    def value(self) -> float:
        """Геттер для свойства 'value', необходимого для QPropertyAnimation."""
        return self._value

    @value.setter
    def value(self, new_value: float) -> None:
        """Сеттер для свойства 'value', обновляет отображаемый текст."""
        self._value = new_value
        # Форматируем текст с учетом типа (целое или с плавающей точкой)
        display_value: Union[int, float]
        if new_value == int(new_value):
            display_value = int(new_value)
        else:
            display_value = round(new_value, 2)
            
        self.setText(self.text_format.format(value=display_value))

    def animate_to(self, end_value: Union[int, float], duration: int = 1500) -> None:
        """
        Запускает анимацию счетчика до конечного значения.
        
        Args:
            end_value: Финальное значение счетчика.
            duration: Длительность анимации в миллисекундах.
        """
        self.animation.setDuration(duration)
        self.animation.setStartValue(self.value)
        self.animation.setEndValue(float(end_value))
        self.animation.start()


# ==============================================================================
#  PulsingButton
# ==============================================================================

class PulsingButton(QWidget):
    """
    Главная круглая кнопка с эффектом "дыхания" (пульсации).
    Оптимизирована для минимизации перерисовок.
    """
    clicked = pyqtSignal()

    def __init__(self, icon: QIcon, text: str = "", parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setMinimumSize(220, 220)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        
        self.icon: QIcon = icon
        self.text: str = text
        self.is_enabled: bool = True
        self._scale: float = 1.0

        # Настраиваем анимацию пульсации
        self.scale_anim = QPropertyAnimation(self, b"scale")
        self.scale_anim.setDuration(2500)
        self.scale_anim.setLoopCount(-1) # Бесконечный цикл
        self.scale_anim.setStartValue(1.0)
        self.scale_anim.setKeyValueAt(0.5, 1.05)
        self.scale_anim.setEndValue(1.0)
        self.scale_anim.setEasingCurve(QEasingCurve.Type.InOutSine)
        self.scale_anim.start()
        
    @pyqtProperty(float)
    def scale(self) -> float:
        """Геттер для свойства 'scale'."""
        return self._scale
    
    @scale.setter
    def scale(self, value: float) -> None:
        """Сеттер для свойства 'scale', запрашивает перерисовку виджета."""
        self._scale = value
        self.update() # Запросить перерисовку

    def setEnabled(self, enabled: bool) -> None:
        """Переопределяем setEnabled для управления анимацией и внешним видом."""
        super().setEnabled(enabled)
        if self.is_enabled == enabled:
            return
            
        self.is_enabled = enabled
        if enabled:
            if self.scale_anim.state() != QPropertyAnimation.State.Running:
                self.scale_anim.start()
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        else:
            self.scale_anim.pause()
            self._scale = 1.0 # Возвращаем в нормальный размер
            self.setCursor(Qt.CursorShape.ArrowCursor)
        self.update()

    def paintEvent(self, event) -> None:
        """Отрисовывает кнопку и ее эффекты."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        center = QPointF(self.rect().center())
        radius = (min(self.width(), self.height()) / 2.0) - 10
        
        # Динамический радиус, зависящий от анимации
        current_radius = radius * self._scale

        # 1. Внешний ореол (пульсирует вместе с кнопкой)
        glow_color = QColor(0, 120, 212, 100) if self.is_enabled else QColor(80, 80, 80, 50)
        gradient = QRadialGradient(center, current_radius)
        gradient.setColorAt(0.8, glow_color)
        gradient.setColorAt(1.0, Qt.GlobalColor.transparent)
        painter.setBrush(gradient)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(center, current_radius, current_radius)
        
        # 2. Основной круг
        bg_color = QColor("#0078D4") if self.is_enabled else QColor("#3e4451")
        border_color = QColor(60, 180, 255, 150) if self.is_enabled else QColor(80, 80, 80)
        
        painter.setBrush(bg_color)
        painter.setPen(QPen(border_color, 2))
        painter.drawEllipse(center, radius * 0.8, radius * 0.8)

        # 3. Иконка
        icon_size = int(radius * 0.7)
        icon_rect = QRectF(0, 0, icon_size, icon_size)
        icon_rect.moveCenter(center)
        self.icon.paint(painter, icon_rect.toRect())
        
        # 4. Текст под иконкой
        if self.text:
            font = painter.font()
            font.setPointSize(10)
            painter.setFont(font)
            painter.setPen(QColor("#FFFFFF"))
            
            text_rect = QRectF(0, 0, radius * 1.5, 40) # Ширина текста зависит от радиуса
            text_rect.moveCenter(QPointF(center.x(), center.y() + radius * 0.6))
            
            # --- ИСПРАВЛЕНИЕ: Используем правильные флаги ---
            # Qt.AlignmentFlag для выравнивания, Qt.TextFlag для переноса
            painter.drawText(text_rect, int(Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap), self.text)
    
    def mousePressEvent(self, event) -> None:
        """Обрабатывает клик мыши."""
        if self.is_enabled and event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
            
    def enterEvent(self, event):
        """Можно добавить эффект при наведении мыши."""
        # Например, немного увеличить кнопку
        # self.scale_anim.pause()
        # self.scale_up_anim = QPropertyAnimation(self, b"scale")
        # ...
        super().enterEvent(event)
        
    def leaveEvent(self, event):
        """Эффект при уходе мыши с виджета."""
        # self.scale_up_anim.stop()
        # if self.is_enabled: self.scale_anim.start()
        super().leaveEvent(event)


# ==============================================================================
#  InfoCardWidget (закомментирован, т.к. не используется в v1.0.0)
# ==============================================================================
#
# class InfoCardWidget(QWidget):
#     """
#     Динамически появляющаяся информационная карточка с иконкой и счетчиком.
#     """
#     def __init__(self, icon: QIcon, title: str, parent=None):
#         super().__init__(parent)
#         # ... код виджета ...
#