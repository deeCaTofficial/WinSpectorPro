# src/winspector/core/modules/__init__.py
"""
Этот пакет содержит независимые, специализированные модули-анализаторы.
Каждый модуль отвечает за свою конкретную область анализа и оптимизации
системы.

Этот __init__.py файл экспортирует все классы анализаторов, чтобы
сделать их доступными для импорта как единый набор инструментов.
"""

# Импортируем все публичные классы из их модулей
from .ai_base import AIBase
from .ai_analyzer import AIAnalyzer
from .ai_communicator import AICommunicator # <-- ДОБАВЛЕНО
from .dynamic_scan import DynamicAnalyzer
from .smart_cleaner import SmartCleaner
from .user_profiler import UserProfiler
from .windows_optimizer import WindowsOptimizer
from .wmi_base import WMIBase # <-- ДОБАВЛЕНО

# Явно определяем, что является публичным API этого пакета.
# Это позволяет импортировать все анализаторы одной строкой, если нужно,
# и делает структуру пакета более ясной.
__all__ = [
    "AIBase",
    "AIAnalyzer",
    "AICommunicator", # <-- ДОБАВЛЕНО
    "DynamicAnalyzer",
    "SmartCleaner",
    "UserProfiler",
    "WindowsOptimizer",
    "WMIBase", # <-- ДОБАВЛЕНО
]