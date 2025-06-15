# src/winspector/gui/widgets/title_bar.py
"""
Кастомный, полностью стилизованный и управляемый TitleBar для приложения,
обеспечивающий перемещение безрамочного окна и кастомные кнопки управления.
"""
from PyQt6.QtWidgets import QWidget, QHBoxLayout, QLabel, QApplication
from PyQt6.QtCore import Qt, QSize, QRectF, QPointF, pyqtSignal
from PyQt6.QtGui import QIcon, QPainter, QColor, QPen, QBrush

class TitleBarButton(QWidget):
    """
    Кастомная круглая, стилизованная кнопка, используемая в TitleBar.
    Отрисовывает символы управления окном вручную для достижения
    единого, четкого стиля и обрабатывает события наведения мыши.
    """
    clicked = pyqtSignal()

    # --- Цветовая палитра (оптимизация) ---
    _BG_COLOR = QColor(44, 48, 56, 150)
    _BORDER_COLOR = QColor(80, 160, 255, 120)
    _SYMBOL_COLOR = QColor(200, 200, 200)
    _HOVER_BG_COLOR = QColor(80, 160, 255, 80)
    _HOVER_SYMBOL_COLOR = QColor(255, 255, 255)

    def __init__(self, symbol: str, parent=None):
        super().__init__(parent)
        self.symbol = symbol
        self.setFixedSize(28, 28)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.is_hovered = False
        
        # Атрибут для кэширования геометрии символа
        self._symbol_rect = QRectF()

    def resizeEvent(self, event):
        """Кэширует геометрию для отрисовки символа при изменении размера."""
        super().resizeEvent(event)
        # Центрируем область 10x10 для отрисовки символа внутри кнопки 28x28
        padding = (self.width() - 10) / 2
        self._symbol_rect = QRectF(padding, padding, 10, 10)

    def paintEvent(self, event):
        """Отрисовывает кнопку и ее символ, используя кэшированную геометрию."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        current_bg = self._HOVER_BG_COLOR if self.is_hovered else self._BG_COLOR
        current_symbol = self._HOVER_SYMBOL_COLOR if self.is_hovered else self._SYMBOL_COLOR

        painter.setPen(QPen(self._BORDER_COLOR, 1))
        painter.setBrush(current_bg)
        painter.drawEllipse(self.rect().adjusted(1, 1, -1, -1))

        # --- Ручная отрисовка символов для идеального вида ---
        pen = QPen(current_symbol)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap) # Сглаженные концы для всех линий
        painter.setPen(pen)

        symbol_rect = self._symbol_rect # Используем кэшированное значение
        painter.setBrush(Qt.BrushStyle.NoBrush)

        if self.symbol == "□": # Развернуть
            pen.setWidthF(1.2)
            painter.setPen(pen)
            painter.drawRect(symbol_rect.toRect())
        elif self.symbol == "—": # Свернуть
            pen.setWidthF(1.5)
            painter.setPen(pen)
            center_y = symbol_rect.center().y()
            painter.drawLine(QPointF(symbol_rect.left(), center_y), QPointF(symbol_rect.right(), center_y))
        elif self.symbol == "✕": # Закрыть
            pen.setWidthF(1.5)
            painter.setPen(pen)
            cross_rect = symbol_rect.adjusted(1.5, 1.5, -1.5, -1.5)
            painter.drawLine(cross_rect.topLeft(), cross_rect.bottomRight())
            painter.drawLine(cross_rect.topRight(), cross_rect.bottomLeft())

    def enterEvent(self, event):
        """Обрабатывает наведение курсора мыши."""
        self.is_hovered = True
        self.update()

    def leaveEvent(self, event):
        """Обрабатывает уход курсора мыши."""
        self.is_hovered = False
        self.update()
        
    def mousePressEvent(self, event):
        """При нажатии левой кнопкой мыши испускаем сигнал 'clicked'."""
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
            event.accept() # Важно: предотвращаем "прокликивание" на TitleBar
        super().mousePressEvent(event)


class TitleBar(QWidget):
    """
    Кастомный TitleBar для управления окном. Включает в себя иконку, заголовок
    и кастомные кнопки управления. Обрабатывает перетаскивание окна.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(45)
        # Атрибут для хранения смещения курсора относительно угла окна
        self.drag_position = None
        self._setup_ui()

    def _setup_ui(self):
        """Создает и настраивает пользовательский интерфейс TitleBar."""
        layout = QHBoxLayout(self)
        layout.setContentsMargins(15, 0, 10, 0)
        layout.setSpacing(10)

        # TODO: Заменить на реальный путь к иконке
        icon_label = QLabel()
        app_icon = QIcon() # QIcon(":/icons/assets/logo.png") 
        icon_label.setPixmap(app_icon.pixmap(QSize(22, 22)))
        
        title_label = QLabel("WinSpector Pro")
        title_label.setStyleSheet("color: white; font-size: 14px; font-weight: 600;")

        layout.addWidget(icon_label)
        layout.addWidget(title_label)
        layout.addStretch()

        self._create_and_connect_buttons(layout)

    def _create_and_connect_buttons(self, layout: QHBoxLayout):
        """Создает и подключает кнопки управления окном."""
        minimize_button = TitleBarButton("—")
        maximize_button = TitleBarButton("□")
        close_button = TitleBarButton("✕")

        minimize_button.clicked.connect(self.window().showMinimized)
        maximize_button.clicked.connect(self.toggle_maximize)
        close_button.clicked.connect(self.window().close)
        
        layout.addWidget(minimize_button)
        layout.addWidget(maximize_button)
        layout.addWidget(close_button)

    def toggle_maximize(self):
        """Переключает состояние окна между развернутым и нормальным."""
        win = self.window()
        if win.isMaximized():
            win.showNormal()
        else:
            win.showMaximized()

    def mousePressEvent(self, event):
        """
        Захватывает начальное смещение курсора для перетаскивания.
        Вызывается только при клике на сам TitleBar, а не на кнопки.
        """
        if event.button() == Qt.MouseButton.LeftButton:
            # Рассчитываем смещение один раз при нажатии.
            # Преобразуем QPoint в QPointF для корректного вычитания.
            self.drag_position = event.globalPosition() - QPointF(self.window().frameGeometry().topLeft())
            event.accept()

    def mouseMoveEvent(self, event):
        """
        Перемещает окно, используя предрассчитанное смещение.
        Это самый легковесный и производительный способ.
        """
        if event.buttons() == Qt.MouseButton.LeftButton and self.drag_position is not None:
            self.window().move((event.globalPosition() - self.drag_position).toPoint())
            event.accept()
            
    def mouseReleaseEvent(self, event):
        """Сбрасывает позицию при отпускании кнопки мыши."""
        self.drag_position = None
        event.accept()


# ==============================================================================
#  Test Runner
# ==============================================================================

if __name__ == '__main__':
    import sys
    from PyQt6.QtWidgets import QMainWindow, QVBoxLayout, QWidget, QLabel

    app = QApplication(sys.argv)

    window = QMainWindow()
    window.setWindowFlags(Qt.WindowType.FramelessWindowHint)
    window.setWindowTitle("Тест TitleBar")
    window.resize(800, 600)
    window.setStyleSheet("background-color: #21252b;")

    title_bar = TitleBar(window)
    
    central_widget = QWidget()
    main_layout = QVBoxLayout(central_widget)
    main_layout.setContentsMargins(0, 0, 0, 0)
    main_layout.setSpacing(0)
    main_layout.addWidget(title_bar)

    content_label = QLabel("Это тестовое содержимое окна.\nПеретащите окно за TitleBar.")
    content_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    content_label.setStyleSheet("color: white; font-size: 20px;")
    main_layout.addWidget(content_label)

    window.setCentralWidget(central_widget)

    window.show()
    sys.exit(app.exec())