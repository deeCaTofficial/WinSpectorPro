# src/winspector/core/offline_planner.py
"""
Построение плана оптимизации без обращения к ИИ.

Нужен в двух случаях: пользователь не задал ключ Gemini или сервис недоступен.
Приложение обязано остаться полезным и в этом режиме — просто без
персонализации.

План строится намеренно осторожнее, чем это сделал бы ИИ:

* берутся только правила с `safety: high` — те, что база знаний считает
  безопасными безусловно;
* службам назначается запуск «вручную», а не отключение: такой шаг обратим и
  служба поднимется, если действительно понадобится;
* UWP-приложения не удаляются вовсе — без анализа профиля нельзя понять,
  нужны ли они этому человеку;
* очищаются только категории мусора с `safety: high`.

Результат всё равно проходит через `PlanValidator`: офлайн-режим не является
поводом обходить проверки безопасности.
"""

from __future__ import annotations

import logging
from typing import Any

from .modules.plan_validator import is_critical_service, rule_targets

logger = logging.getLogger(__name__)

SAFE_LEVEL = "high"


def _available_service_names(components: dict[str, Any] | None) -> set[str]:
    """Имена служб, реально присутствующих в системе."""
    services = (components or {}).get("services") or []
    names: set[str] = set()
    for service in services:
        if isinstance(service, dict) and service.get("name"):
            names.add(str(service["name"]).strip().lower())
    return names


def build_action_plan(
    knowledge_base: dict[str, Any],
    components: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """
    Составляет список действий по правилам базы знаний.

    Действие попадает в план, только если соответствующая служба существует
    в системе: предлагать изменения для отсутствующих компонентов бессмысленно.
    """
    available = _available_service_names(components)
    if not available:
        logger.info("Список служб пуст — план действий не составляется.")
        return []

    plan: list[dict[str, Any]] = []
    seen: set[str] = set()

    for rule in knowledge_base.get("optimization_rules") or []:
        if not isinstance(rule, dict):
            continue
        if str(rule.get("safety", "")).lower() != SAFE_LEVEL:
            continue
        if str(rule.get("type", "service")).lower() != "service":
            continue

        description = str(rule.get("description_ru") or "").strip()

        for target in rule_targets(rule):
            if target in seen or target not in available or is_critical_service(target):
                continue
            seen.add(target)
            plan.append(
                {
                    "type": "service",
                    "id": target,
                    # Обратимое действие: служба запустится по требованию.
                    "action": "set_manual",
                    "reason": f"Knowledge base rule {rule.get('id')} (safety: high)",
                    "user_explanation_ru": description
                    or f"Служба «{target}» переведена в запуск по требованию.",
                }
            )

    logger.info("Офлайн-план: отобрано %d действий из базы знаний.", len(plan))
    return plan


def build_cleanup_plan(
    knowledge_base: dict[str, Any],
    junk_report: dict[str, Any] | None,
) -> dict[str, dict[str, bool]]:
    """
    Решает, какие категории мусора чистить без участия ИИ.

    Разрешаются только категории с `safety: high` и только те, которые нашёл
    наш сканер.
    """
    rules = {
        str(rule["category_id"]): rule
        for rule in (knowledge_base.get("cleanup_rules") or [])
        if isinstance(rule, dict) and rule.get("category_id")
    }

    cleanup_plan: dict[str, dict[str, bool]] = {}
    for category_id in junk_report or {}:
        rule = rules.get(category_id)
        safe = bool(rule) and str(rule.get("safety", "")).lower() == SAFE_LEVEL
        cleanup_plan[category_id] = {"clean": safe}

    approved = sum(1 for decision in cleanup_plan.values() if decision["clean"])
    logger.info(
        "Офлайн-план очистки: одобрено %d из %d найденных категорий.",
        approved,
        len(cleanup_plan),
    )
    return cleanup_plan


def build_plan(
    knowledge_base: dict[str, Any],
    components: dict[str, Any] | None,
    junk_report: dict[str, Any] | None,
) -> dict[str, Any]:
    """Собирает полный план в том же формате, что возвращает ИИ."""
    return {
        "action_plan": build_action_plan(knowledge_base, components),
        "cleanup_plan": build_cleanup_plan(knowledge_base, junk_report),
    }
