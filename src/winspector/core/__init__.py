# src/winspector/core/__init__.py
"""
Этот пакет содержит всю основную логику и "мозг" приложения WinSpector Pro.

Он не зависит от GUI и отвечает за анализ, принятие решений и выполнение
оптимизаций.

Этот __init__.py файл экспортирует основной класс ядра, чтобы сделать
импорты из других частей приложения более чистыми и удобными.
"""

# Импортируем основной класс-оркестратор из его модуля
from .analyzer import WinSpectorCore

# Явно определяем, что является публичным API этого пакета.
# Теперь, вместо `from src.winspector.core.analyzer import WinSpectorCore`,
# можно будет использовать более короткий и чистый импорт:
# `from src.winspector.core import WinSpectorCore`.
__all__ = [
    "WinSpectorCore",
]