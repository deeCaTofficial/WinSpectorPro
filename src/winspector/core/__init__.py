# src/winspector/core/__init__.py
"""
Этот пакет содержит всю основную логику и "мозг" приложения WinSpector Pro.

Он не зависит от GUI и отвечает за анализ, принятие решений и выполнение
оптимизаций.

`WinSpectorCore` доступен как `from src.winspector.core import WinSpectorCore`,
но импортируется лениво (PEP 562). Прямой импорт здесь тянул за собой всё
ядро вместе с SDK Gemini — около 0,7 с. Платил за это в том числе дочерний
процесс пула WMI: ему нужен один модуль `wmi_workers`, а при загрузке пакета
он получал и `google.genai`, и `httpx`, и `pydantic`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .analyzer import WinSpectorCore

__all__ = ["WinSpectorCore"]


def __getattr__(name: str) -> object:
    if name == "WinSpectorCore":
        from .analyzer import WinSpectorCore

        return WinSpectorCore
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
