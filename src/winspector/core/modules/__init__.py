# src/winspector/core/modules/__init__.py
"""
Специализированные модули ядра.

Каждый модуль отвечает за свою область: диалог с ИИ, валидацию плана,
профилирование, очистку и применение изменений в Windows.
"""

from .ai_analyzer import AIAnalyzer
from .ai_base import AIBase
from .ai_communicator import AICommunicator
from .dynamic_scan import DynamicAnalyzer
from .plan_validator import PlanValidator
from .smart_cleaner import SmartCleaner
from .user_profiler import UserProfiler
from .windows_optimizer import WindowsOptimizer

__all__ = [
    "AIAnalyzer",
    "AIBase",
    "AICommunicator",
    "DynamicAnalyzer",
    "PlanValidator",
    "SmartCleaner",
    "UserProfiler",
    "WindowsOptimizer",
]
