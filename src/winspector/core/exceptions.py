# src/winspector/core/exceptions.py
"""
Иерархия исключений WinSpector Pro.

Единая иерархия позволяет вышестоящим слоям (GUI) отличать ожидаемые
проблемы (недоступность ИИ, повреждённая база знаний) от настоящих багов
и показывать пользователю осмысленное сообщение вместо трейсбека.
"""

from __future__ import annotations

from typing import Any


class WinSpectorError(Exception):
    """Базовое исключение приложения."""


class KnowledgeBaseError(WinSpectorError):
    """База знаний отсутствует, повреждена или не проходит валидацию схемы."""


class RestorePointError(WinSpectorError):
    """Не удалось создать точку восстановления системы."""


class AIError(WinSpectorError):
    """Базовая ошибка взаимодействия с ИИ."""


class AIUnavailableError(AIError):
    """API недоступен: сеть, таймаут, квота, ошибка сервера."""


class AIContentBlockedError(AIError):
    """Ответ заблокирован фильтрами безопасности провайдера."""

    def __init__(self, message: str, block_reason: Any = None) -> None:
        super().__init__(message)
        self.block_reason = block_reason


class AIResponseError(AIError):
    """Ответ получен, но его не удалось разобрать или он не прошёл валидацию."""


class PlanValidationError(WinSpectorError):
    """План от ИИ структурно некорректен и не может быть исполнен."""
