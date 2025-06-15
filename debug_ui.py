# debug_ui.py - ТЕСТ 2: Проверка NeuralBackgroundWidget

import sys
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QStackedWidget, QStackedLayout, QFrame
)
from PyQt6.QtGui import QIcon
from PyQt6.QtCore import Qt

# --- Импортируем только РЕАЛЬНЫЙ фон ---
from src.winspector.gui.widgets.neural_background import NeuralBackgroundWidget

# --- ВИДЖЕТ-ЗАГЛУШКА для кнопки ---
class PulsingButtonMock(QLabel):
    def __init__(self, text="Pulsing Button"):
        super().__init__(text)
        self.setFixedSize(220, 220)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet("background-color: #0078D4; color: white; border-radius: 110px;")

class DebugWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("UI Debug Window - Testing NeuralBackground")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMinimumSize(850, 700)
        self.resize(850, 700)

        # 1. Главный контейнер
        container = QWidget()
        self.setCentralWidget(container)

        # 2. Корневой layout (фон + передний план)
        root_layout = QStackedLayout(container)
        root_layout.setStackingMode(QStackedLayout.StackingMode.StackAll)

        # Используем РЕАЛЬНЫЙ фон
        background = NeuralBackgroundWidget()
        background.start_animation()
        root_layout.addWidget(background)

        # 3. Контейнер переднего плана ("стекло")
        foreground_container = QFrame()
        foreground_container.setObjectName("GlassContainer")
        foreground_container.setStyleSheet("""
            #GlassContainer {
                background-color: rgba(33, 37, 43, 0.85);
                border-radius: 15px;
                border: 1px solid rgba(80, 160, 255, 0.4);
            }
        """)
        root_layout.addWidget(foreground_container)

        # 4. Главный layout для контента
        main_layout = QVBoxLayout(foreground_container)
        main_layout.setContentsMargins(10, 45, 10, 10)
        main_layout.setSpacing(10)

        title = QLabel("WinSpector Pro")
        title.setStyleSheet("font-size: 32px; font-weight: 600; color: white;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        main_layout.addWidget(title)
        
        # 5. Стек для страниц
        self.stacked_widget = QStackedWidget()
        main_layout.addWidget(self.stacked_widget, 1)

        # 6. Создаем и добавляем домашнюю страницу
        home_page = self._create_home_page()
        self.stacked_widget.addWidget(home_page)
        self.stacked_widget.setCurrentWidget(home_page)

        # TitleBar пока не добавляем

    def _create_home_page(self) -> QWidget:
        page = QWidget()
        layout = QHBoxLayout(page)
        
        # --- Используем ЗАГЛУШКУ для кнопки ---
        self.pulsing_button = PulsingButtonMock()
        
        layout.addStretch()
        layout.addWidget(self.pulsing_button)
        layout.addStretch()
        
        return page

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = DebugWindow()
    window.show()
    sys.exit(app.exec())