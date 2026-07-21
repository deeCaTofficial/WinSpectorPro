# src/winspector/core/modules/ai_analyzer.py
"""
Генерация плана оптимизации с помощью Gemini.

Модуль отвечает только за диалог с моделью: собрать промпт, получить
структурированный ответ и передать его валидатору. Решение о том, что
безопасно исполнять, принимает `plan_validator`, а не модель.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from ..exceptions import AIResponseError
from .ai_base import AIBase
from .plan_validator import ALLOWED_ACTIONS, ALLOWED_TYPES, PlanValidator

logger = logging.getLogger(__name__)

# Поля базы знаний, бесполезные для модели. `provenance` содержит длинные
# комментарии верификации — на 113 правилах это тысячи лишних токенов.
_KB_FIELDS_TO_STRIP = ("provenance",)

_SYSTEM_INSTRUCTION = (
    "You are an expert Windows optimization engineer. You produce conservative, "
    "reversible optimization plans. You never touch components required for the "
    "system to boot, authenticate users, apply updates, or protect the machine. "
    "When in doubt, you leave the component alone."
)

# Схема ответа. `cleanup_decisions` — список, а не словарь: структурированный
# вывод Gemini не поддерживает объекты с произвольными ключами.
PLAN_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action_plan": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": sorted(ALLOWED_TYPES)},
                    "id": {"type": "string"},
                    "action": {"type": "string", "enum": sorted(ALLOWED_ACTIONS)},
                    "package_full_name": {"type": "string"},
                    "reason": {"type": "string"},
                    "user_explanation_ru": {"type": "string"},
                },
                "required": ["type", "id", "action", "reason", "user_explanation_ru"],
            },
        },
        "cleanup_decisions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "category_id": {"type": "string"},
                    "clean": {"type": "boolean"},
                    "reason": {"type": "string"},
                },
                "required": ["category_id", "clean"],
            },
        },
    },
    "required": ["action_plan", "cleanup_decisions"],
}


def strip_kb_noise(rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Убирает служебные поля базы знаний перед отправкой модели."""
    cleaned: list[dict[str, Any]] = []
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        cleaned.append({k: v for k, v in rule.items() if k not in _KB_FIELDS_TO_STRIP})
    return cleaned


class AIAnalyzer(AIBase):
    """Формирует план оптимизации и передаёт его на валидацию."""

    def _create_plan_prompt(
        self,
        system_data: dict[str, Any],
        profiles: list[str],
        knowledge_base: dict[str, Any],
    ) -> str:
        optimization_rules = strip_kb_noise(knowledge_base.get("optimization_rules") or [])
        junk_categories = sorted((system_data.get("junk_files_report") or {}).keys())

        return f"""
Create a Windows optimization plan for a user with these profiles: {json.dumps(profiles)}.

## Part 1 — action_plan
Review `system_components` below and select non-essential services and UWP apps.

Rules:
- Use `id` values EXACTLY as they appear in `system_components` (real service
  names such as `MapsBroker`, not the labels used in the knowledge base).
- Prefer `set_manual` over `disable`: it is reversible and lower risk.
- Never propose actions on components required for boot, login, networking,
  Windows Update, or security.
- Skip anything that the user's profiles rely on.
- For UWP apps include `package_full_name` when it is present in the data.
- `user_explanation_ru` must be one short sentence in Russian, addressed to a
  non-technical user.
- An empty plan is a valid and correct answer when nothing is safe to change.

## Part 2 — cleanup_decisions
Provide one entry for EVERY category id listed below, and no others:
{json.dumps(junk_categories, ensure_ascii=False)}

Set `clean` to false when the data could matter to this user — for example
package-manager and build caches for a Developer, or media caches for a
ContentCreator. Otherwise set it to true.

## KNOWLEDGE BASE (safety reference)
{json.dumps(optimization_rules, indent=1, ensure_ascii=False)}

## SYSTEM SNAPSHOT
{json.dumps(system_data, indent=1, ensure_ascii=False, default=str)}
""".strip()

    @staticmethod
    def _decisions_to_cleanup_plan(raw_plan: dict[str, Any]) -> dict[str, Any]:
        """Преобразует список решений об очистке во внутренний формат-словарь."""
        decisions = raw_plan.get("cleanup_decisions")
        if decisions is None:
            # Совместимость со старым форматом ответа.
            return raw_plan.get("cleanup_plan") or {}

        if not isinstance(decisions, list):
            raise AIResponseError("'cleanup_decisions' должен быть списком.")

        cleanup_plan: dict[str, Any] = {}
        for entry in decisions:
            if not isinstance(entry, dict):
                continue
            category_id = entry.get("category_id")
            if not category_id:
                continue
            cleanup_plan[str(category_id)] = {"clean": bool(entry.get("clean"))}
        return cleanup_plan

    async def generate_distillation_plan(
        self,
        system_data: dict[str, Any],
        profiles: list[str],
        knowledge_base: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Запрашивает план у модели и возвращает уже провалидированную версию.

        Raises:
            AIError: если модель недоступна или вернула неразбираемый ответ.
            ValueError: если структура плана принципиально некорректна.
        """
        prompt = self._create_plan_prompt(system_data, profiles, knowledge_base)

        raw_plan = await self._generate_json(
            prompt,
            context="generate_distillation_plan",
            response_schema=PLAN_RESPONSE_SCHEMA,
            system_instruction=_SYSTEM_INSTRUCTION,
            temperature=0.1,
            max_output_tokens=8192,
            use_cache=False,
        )

        if not isinstance(raw_plan, dict):
            raise AIResponseError(f"Ожидался объект плана, получен {type(raw_plan).__name__}.")

        normalized = {
            "action_plan": raw_plan.get("action_plan") or [],
            "cleanup_plan": self._decisions_to_cleanup_plan(raw_plan),
        }

        validator = PlanValidator(
            knowledge_base=knowledge_base,
            user_profiles=profiles,
            known_junk_categories=(system_data.get("junk_files_report") or {}).keys(),
        )
        safe_plan = validator.validate(normalized)

        logger.info(
            "План от ИИ получен и провалидирован: %d действий одобрено.",
            len(safe_plan["action_plan"]),
        )
        return safe_plan
