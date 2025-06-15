# src/winspector/gui/widgets/animated_widgets.py
"""
Кастомные анимированные виджеты для создания современного
и динамичного пользовательского интерфейса WinSpector Pro.
"""
from PyQt6.QtWidgets import QWidget, QLabel
from PyQt6.QtGui import (
    QIcon, QPainter, QColor, QBrush, QPen, QFont, QRadialGradient,
    QPainterPath, QRegion
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
        """Свойство для анимации, хранящее текущее числовое значение."""
        return self._value

    @value.setter
    def value(self, new_value: float) -> None:
        """
        Устанавливает текущее значение счетчика и обновляет текст.
        Автоматически форматирует число: целые числа отображаются без
        дробной части, числа с плавающей точкой округляются.
        """
        self._value = new_value
        display_value: Union[int, float]
        # Если значение по факту целое (например, 25.0), отображаем его как int (25)
        if new_value == int(new_value):
            display_value = int(new_value)
        else:
            # Иначе округляем до 2 знаков после запятой
            display_value = round(new_value, 2)
        self.setText(self.text_format.format(value=display_value))

    def animate_to(self, end_value: Union[int, float], duration: int = 1500) -> None:
        """
        Запускает анимацию счетчика от текущего значения до конечного.
        
        Args:
            end_value: Конечное значение, до которого нужно досчитать.
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
    Главная круглая кнопка с гладкими краями и круглой областью клика.
    Использует setMask с трюком для сглаживания и кэширует геометрию
    для максимальной производительности отрисовки.
    """
    clicked = pyqtSignal()

    # --- Цветовая палитра для состояний (оптимизация) ---
    _ENABLED_GLOW = QColor(0, 120, 212, 100)
    _DISABLED_GLOW = QColor(80, 80, 80, 50)
    _ENABLED_GLASS = QColor(44, 48, 56, 180)
    _DISABLED_GLASS = QColor(60, 60, 60, 150)
    _ENABLED_BORDER = QColor(80, 160, 255, 150)
    _DISABLED_BORDER = QColor(80, 80, 80, 100)
    _INNER_GLOW_START = QColor(80, 160, 255, 80)
    _INNER_GLOW_END = QColor(80, 160, 255, 0)

    def __init__(self, icon: QIcon, text: str = "", parent: Optional[QWidget] = None):
        super().__init__(parent)
        
        self.setMinimumSize(220, 220)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        
        self.icon: QIcon = icon
        self.text: str = text
        self.is_enabled: bool = True
        self._scale: float = 1.0

        # --- Атрибуты для кэширования геометрии (оптимизация) ---
        self._center: QPointF = QPointF()
        self._radius: float = 0.0
        self._icon_rect: QRectF = QRectF()

        # Настраиваем анимацию пульсации
        self.scale_anim = QPropertyAnimation(self, b"scale")
        self.scale_anim.setDuration(2500)
        self.scale_anim.setLoopCount(-1)
        self.scale_anim.setStartValue(1.0)
        self.scale_anim.setKeyValueAt(0.5, 1.05)
        self.scale_anim.setEndValue(1.0)
        self.scale_anim.setEasingCurve(QEasingCurve.Type.InOutSine)
        self.scale_anim.start()
        
    def start_animation(self):
        """Запускает анимацию, если она не активна."""
        if self.scale_anim.state() != QPropertyAnimation.State.Running:
            self.scale_anim.start()

    def stop_animation(self):
        """Останавливает анимацию пульсации."""
        if self.scale_anim.state() == QPropertyAnimation.State.Running:
            self.scale_anim.stop()
            
    def pause(self):
        """Приостанавливает анимацию."""
        if self.scale_anim.state() == QPropertyAnimation.State.Running:
            self.scale_anim.pause()

    def resume(self):
        """Возобновляет анимацию."""
        if self.scale_anim.state() == QPropertyAnimation.State.Paused:
            self.scale_anim.resume()

    @pyqtProperty(float)
    def scale(self) -> float:
        return self._scale
    
    @scale.setter
    def scale(self, value: float) -> None:
        self._scale = value
        self.update()

    def setEnabled(self, enabled: bool) -> None:
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
            self._scale = 1.0
            self.setCursor(Qt.CursorShape.ArrowCursor)
        self.update()

    def resizeEvent(self, event):
        """
        Обновляет круглую маску виджета и кэширует геометрические
        параметры при изменении размера. Это ключевая оптимизация,
        позволяющая избежать тяжелых вычислений в paintEvent.
        """
        super().resizeEvent(event)
        # Маска обрезает виджет до формы эллипса, обеспечивая
        # идеально круглую область для кликов и отрисовки.
        self.setMask(QRegion(self.rect(), QRegion.RegionType.Ellipse))

        # Кэшируем значения, зависящие от размера.
        offset = 1 # Трюк для сглаживания
        paint_rect = self.rect().adjusted(offset, offset, -offset, -offset)
        
        self._center = QPointF(paint_rect.center())
        self._radius = (min(paint_rect.width(), paint_rect.height()) / 2.0) - 10
        
        icon_size = int(self._radius)
        self._icon_rect = QRectF(0, 0, icon_size, icon_size)
        self._icon_rect.moveCenter(self._center)

    def paintEvent(self, event) -> None:
        """
        Отрисовывает кнопку, используя предварительно кэшированные
        геометрические параметры для максимальной производительности.
        """
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        # --- Оптимизация: используем локальные переменные и кэшированные атрибуты ---
        is_enabled = self.is_enabled
        scale = self._scale
        icon = self.icon
        center = self._center
        radius = self._radius
        icon_rect = self._icon_rect
        
        # --- Отрисовка ---

        # 1. Внешний пульсирующий ореол
        glow_color = self._ENABLED_GLOW if is_enabled else self._DISABLED_GLOW
        current_radius = radius * scale
        
        gradient = QRadialGradient(center, current_radius)
        gradient.setColorAt(0.8, glow_color)
        gradient.setColorAt(1.0, Qt.GlobalColor.transparent)
        painter.setBrush(gradient)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(center, current_radius, current_radius)

        # 2. Основной "стеклянный" круг и его граница
        glass_bg_color = self._ENABLED_GLASS if is_enabled else self._DISABLED_GLASS
        border_color = self._ENABLED_BORDER if is_enabled else self._DISABLED_BORDER
        
        border_pen = QPen(border_color, 2)
        
        painter.setPen(border_pen)
        painter.setBrush(glass_bg_color)
        painter.drawEllipse(center, radius, radius)

        # 3. Внутреннее свечение
        inner_glow_gradient = QRadialGradient(center, radius)
        inner_glow_gradient.setColorAt(0, self._INNER_GLOW_START) 
        inner_glow_gradient.setColorAt(1, self._INNER_GLOW_END)
        
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(inner_glow_gradient)
        painter.drawEllipse(center, radius, radius)
        
        # 4. Иконка
        if not is_enabled:
            painter.setOpacity(0.5)
            
        icon.paint(painter, icon_rect.toRect())
        painter.setOpacity(1.0)

    def mousePressEvent(self, event) -> None:
        """Обрабатывает клик мыши. Проверка не нужна, т.к. маска работает."""
        if self.isEnabled():
            self.clicked.emit()
            event.accept()
        else:
            event.ignore()

# ==============================================================================
#  Test Runner
# ==============================================================================

if __name__ == '__main__':
    import sys
    import os
    import random
    
    # Этот блок позволяет запускать скрипт напрямую для тестирования.
    # Он настраивает путь Python для импорта из каталога 'src'.
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_src_dir = os.path.abspath(os.path.join(script_dir, '..', '..', '..'))
    if project_src_dir not in sys.path:
        sys.path.insert(0, project_src_dir)
        
    from winspector.gui.widgets.neural_background import NeuralBackgroundWidget
    from PyQt6.QtWidgets import QApplication, QMainWindow

    app = QApplication(sys.argv)

    # --- Настройка главного окна ---
    window = QMainWindow()
    window.setWindowTitle("Тест анимированных виджетов")
    window.setFixedSize(800, 600)

    # --- Фон ---
    # Используем NeuralBackgroundWidget для имитации реального окружения приложения.
    background = NeuralBackgroundWidget()
    window.setCentralWidget(background)
    
    # --- Тестируемые виджеты ---
    # Примечание: предполагается, что 'assets/rocket.png' находится в папке 'assets' корневого каталога проекта.
    try:
        project_root = os.path.abspath(os.path.join(project_src_dir, '..'))
        icon_path = os.path.join(project_root, 'assets', 'rocket.png')
        scan_icon = QIcon(icon_path)
        if scan_icon.isNull():
            print(f"Предупреждение: Иконка не найдена по пути '{icon_path}'. Используется пустая иконка.")
            scan_icon = QIcon()
    except Exception as e:
        print(f"Не удалось загрузить иконку: {e}")
        scan_icon = QIcon()
        
    pulsing_button = PulsingButton(icon=scan_icon, parent=background)
    counter_label = AnimatedCounterLabel(text_format="Найдено угроз: {value}", parent=background)
    counter_label.setObjectName("TestCounterLabel")
    counter_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    
    # --- Стилизация ---
    # Стилизуем метку, чтобы она была хорошо видна на темном фоне.
    counter_label.setStyleSheet("""
        #TestCounterLabel {
            font-family: "Segoe UI", "Roboto", "Helvetica Neue", Arial, sans-serif;
            color: #E0E1DD;
            font-size: 22px;
            font-weight: 600;
        }
    """)

    # --- Позиционирование ---
    # Позиционируем виджеты вручную, так как они являются дочерними для QWidget, а не для layout.
    bg_rect = background.rect()
    center_x = bg_rect.width() / 2
    center_y = bg_rect.height() / 2
    
    button_size = 220 
    pulsing_button.setGeometry(
        int(center_x - button_size / 2),
        int(center_y - button_size / 2 - 20),
        button_size,
        button_size
    )

    label_width = 300
    label_height = 40
    counter_label.setGeometry(
        int(center_x - label_width / 2),
        int(pulsing_button.geometry().bottom() + 10),
        label_width,
        label_height
    )
    
    # --- Логика ---
    # Эта функция демонстрирует функциональность виджетов.
    def run_test_animation():
        if not pulsing_button.isEnabled():
            return
            
        print("Кнопка нажата. Запуск тестовой анимации.")
        
        pulsing_button.setEnabled(False)
        random_value = random.randint(50, 250)
        counter_label.animate_to(random_value, duration=1500)
        
        QTimer.singleShot(1600, lambda: pulsing_button.setEnabled(True))
        
    pulsing_button.clicked.connect(run_test_animation)
    
    # --- Запуск ---
    background.start_animation()
    window.show()

    sys.exit(app.exec())