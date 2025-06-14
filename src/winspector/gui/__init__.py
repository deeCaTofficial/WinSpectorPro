# src/winspector/gui/__init__.py
"""
Этот пакет содержит все компоненты, связанные с графическим
пользовательским интерфейсом (GUI) приложения WinSpector Pro.

Этот __init__.py файл экспортирует основные классы GUI, чтобы сделать
импорты из других частей приложения более чистыми и удобными.
"""

# Импортируем основной класс окна из его модуля
from .main_window import MainWindow

# Явно определяем, что является публичным API этого пакета.
# Теперь, вместо `from src.winspector.gui.main_window import MainWindow`,
# можно будет использовать более короткий и чистый импорт:
# `from src.winspector.gui import MainWindow`.
__all__ = [
    "MainWindow",
]