# src/winspector/core/modules/ai_communicator.py
"""
«Коммуникационные» задачи ИИ: определение профиля пользователя и подготовка
итогового отчёта. Здесь нет решений, влияющих на систему, — только
интерпретация уже собранных данных.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from ..exceptions import AIError
from .ai_base import AIBase
from .plan_validator import CANONICAL_PROFILES, normalize_profile

logger = logging.getLogger(__name__)

DEFAULT_PROFILE = "HomeUser"

# Значения совпадают с каноническим списком в `plan_validator`, но записаны
# в CamelCase — так модель отвечает заметно стабильнее.
_PROFILE_CHOICES = [
    "Gamer",
    "Developer",
    "Designer",
    "OfficeWorker",
    "Streamer",
    "ContentCreator",
    "AudioEngineer",
    "PowerUser",
    "HomeUser",
]

PROFILE_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "profiles": {
            "type": "array",
            "items": {"type": "string", "enum": _PROFILE_CHOICES},
        },
        "reasoning": {"type": "string"},
    },
    "required": ["profiles"],
}

_PROFILE_SYSTEM_INSTRUCTION = (
    "You classify how a Windows machine is used, based on installed software, "
    "shortcuts, hardware and folder markers. A machine usually serves more than "
    "one purpose, so returning several profiles is normal and expected."
)


def _format_bytes(value: int) -> str:
    """Человекочитаемый размер: 1536 -> '1.5 КБ'."""
    if value <= 0:
        return "0 байт"
    for unit, threshold in (("ГБ", 1024**3), ("МБ", 1024**2), ("КБ", 1024)):
        if value >= threshold:
            return (
                f"{value / threshold:.2f} {unit}"
                if unit == "ГБ"
                else (f"{value / threshold:.1f} {unit}")
            )
    return f"{value} байт"


class AICommunicator(AIBase):
    """Определяет профиль пользователя и формирует отчёт на русском языке."""

    # --- Профилирование ---------------------------------------------------

    def _create_profile_prompt(self, system_data: dict[str, Any], kb_config: dict[str, Any]) -> str:
        return f"""
Determine which profiles describe this machine. Return every profile that
applies; return ["HomeUser"] when nothing more specific stands out.

Weigh `shortcuts`, `installed_software` and `user_folder_stats` most heavily —
they reflect what the person actually runs. Hardware alone is weak evidence:
a powerful GPU does not by itself make someone a Gamer.

## PROFILER HINTS (keywords per profile)
{json.dumps(kb_config, indent=1, ensure_ascii=False)}

## SYSTEM DATA
{json.dumps(system_data, indent=1, ensure_ascii=False, default=str)}
""".strip()

    async def determine_user_profile(
        self, system_data: dict[str, Any], kb_config: dict[str, Any] | None = None
    ) -> list[str]:
        """
        Возвращает список профилей пользователя.

        Ошибки ИИ не прерывают оптимизацию: при любой проблеме возвращается
        консервативный профиль `HomeUser`, для которого правила самые щадящие.
        """
        prompt = self._create_profile_prompt(system_data, kb_config or {})
        try:
            data = await self._generate_json(
                prompt,
                context="determine_user_profile",
                response_schema=PROFILE_RESPONSE_SCHEMA,
                system_instruction=_PROFILE_SYSTEM_INSTRUCTION,
                temperature=0.2,
                max_output_tokens=1024,
            )
        except AIError as exc:
            logger.error(
                "Не удалось определить профиль (%s). Используется '%s'.",
                exc,
                DEFAULT_PROFILE,
            )
            return [DEFAULT_PROFILE]

        profiles = data.get("profiles") if isinstance(data, dict) else None
        valid = [
            p
            for p in (profiles or [])
            if isinstance(p, str) and normalize_profile(p) in CANONICAL_PROFILES
        ]
        if not valid:
            logger.warning(
                "ИИ вернул некорректный набор профилей: %r. Используется '%s'.",
                profiles,
                DEFAULT_PROFILE,
            )
            return [DEFAULT_PROFILE]

        # Убираем повторы, сохраняя порядок.
        unique = list(dict.fromkeys(valid))
        logger.info("Определены профили пользователя: %s", ", ".join(unique))
        return unique

    # --- Итоговый отчёт ---------------------------------------------------

    def _create_report_prompt(
        self,
        summary: dict[str, Any],
        plan: list[dict[str, Any]],
        profiles: list[str],
    ) -> str:
        debloat = summary.get("debloat") or {}
        cleanup = summary.get("cleanup") or {}
        empty_folders = summary.get("empty_folders") or {}

        completed = debloat.get("completed") or []
        failed = debloat.get("failed") or []
        cleaned_bytes = int(cleanup.get("cleaned_size_bytes", 0) or 0)
        deleted_folders = int(empty_folders.get("deleted_folders_count", 0) or 0)
        total_actions = len(completed)

        if total_actions > 5 or cleaned_bytes > 500 * 1024 * 1024:
            tone = "Positive and celebratory. Use emojis such as ✅, 🚀, 💪."
            headline = "## 🚀 Отличная работа! Ваша система оптимизирована."
        elif total_actions > 0 or cleaned_bytes > 0:
            tone = "Calm and informative, like a helpful assistant."
            headline = "## ✅ Оптимизация завершена."
        else:
            tone = (
                "Reassuring and professional. Explain that the system was already "
                "in good shape and that finding nothing to change is a good result."
            )
            headline = "## 🛡️ Ваша система в прекрасном состоянии!"

        actions_text = (
            "\n".join(
                f"- {item['user_explanation_ru']}"
                for item in completed
                if isinstance(item, dict) and item.get("user_explanation_ru")
            )
            or "Изменений в системных компонентах не потребовалось."
        )

        return f"""
Write a short report in Russian Markdown for the user of a Windows optimizer.

USER PROFILES: {json.dumps(profiles)}
TONE: {tone}
SUGGESTED HEADLINE: {headline}

METRICS
- Освобождено места: {_format_bytes(cleaned_bytes)}
- Изменено компонентов: {total_actions}
- Не удалось изменить: {len(failed)}
- Удалено пустых папок: {deleted_folders}

ACTIONS PERFORMED
{actions_text}

REQUIREMENTS
1. Start with the headline.
2. Add a short metrics block.
3. Add a "Что было сделано:" section listing the actions above.
4. Close with one reassuring sentence mentioning that a restore point was
   created before any changes.
5. Do not invent numbers or actions that are not listed above.
6. Keep it under 200 words.
""".strip()

    async def generate_final_report(
        self,
        summary: dict[str, Any],
        plan: list[dict[str, Any]],
        profiles: list[str],
    ) -> str:
        """Формирует отчёт. При сбое ИИ возвращает локальный текстовый отчёт."""
        prompt = self._create_report_prompt(summary, plan, profiles)
        try:
            return await self._generate(
                prompt,
                context="generate_final_report",
                temperature=0.6,
                max_output_tokens=2048,
                use_cache=False,
            )
        except AIError as exc:
            logger.error("Не удалось сгенерировать отчёт через ИИ: %s", exc)
            return self.build_offline_report(summary)

    @staticmethod
    def build_offline_report(summary: dict[str, Any]) -> str:
        """
        Резервный отчёт без обращения к ИИ.

        Оптимизация уже выполнена — пользователь обязан увидеть результат,
        даже если сеть недоступна.
        """
        debloat = summary.get("debloat") or {}
        cleanup = summary.get("cleanup") or {}
        empty_folders = summary.get("empty_folders") or {}
        completed = debloat.get("completed") or []

        lines = [
            "## ✅ Оптимизация завершена",
            "",
            f"- **Освобождено места:** {_format_bytes(int(cleanup.get('cleaned_size_bytes', 0) or 0))}",
            f"- **Изменено компонентов:** {len(completed)}",
            f"- **Удалено пустых папок:** {empty_folders.get('deleted_folders_count', 0)}",
        ]

        explanations = [
            f"- {item['user_explanation_ru']}"
            for item in completed
            if isinstance(item, dict) and item.get("user_explanation_ru")
        ]
        if explanations:
            lines += ["", "### Что было сделано:", *explanations]

        lines += [
            "",
            "_Перед изменениями была создана точка восстановления системы._",
            "",
            "> Отчёт сформирован локально: сервис ИИ был недоступен.",
        ]
        return "\n".join(lines)

    # --- Обратная связь для разработчиков ---------------------------------

    async def get_ai_suggestions_for_improvement(self, **kwargs: Any) -> str:
        """Анализирует прошедшую сессию и предлагает улучшения продукта."""
        prompt = f"""
You are a lead engineer reviewing one optimization session of a Windows tool.
Suggest 3-5 concrete technical improvements for future versions, focused on
safety, accuracy of the plan, and performance.

SESSION DATA
{json.dumps(kwargs, indent=1, ensure_ascii=False, default=str)}

Respond in Russian Markdown.
""".strip()
        return await self._generate(
            prompt,
            context="ai_suggestions",
            temperature=0.8,
            max_output_tokens=2048,
            use_cache=False,
        )
