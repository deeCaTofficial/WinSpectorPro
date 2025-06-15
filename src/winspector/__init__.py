# src/winspector/__init__.py
"""
Инициализация пакета WinSpector Pro.

Этот файл определяет основные метаданные приложения и делает их
доступными для импорта из других частей программы.
"""

__version__ = "1.0.2"
__author__ = "CLC corporation"
# Для связи с разработчиками используйте раздел "Issues" в репозитории GitHub.

# Основные метаданные приложения, сгруппированные для удобства
APP_NAME = "WinSpector Pro"
APP_VERSION = __version__
ORG_NAME = __author__

# Определяем публичный API пакета.
# Это список имен, которые будут импортированы при выполнении 'from winspector import *'
__all__ = [
    "__version__",
    "__author__",
    "APP_NAME",
    "APP_VERSION",
    "ORG_NAME",
]