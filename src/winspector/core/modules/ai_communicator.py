# src/winspector/core/modules/ai_communicator.py
"""
«Коммуникационные» задачи ИИ: определение профиля пользователя и подготовка
итогового отчёта. Здесь нет решений, влияющих на систему, — только
интерпретация уже собранных данных.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import PureWindowsPath
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


def _leftovers_lines(leftovers: dict[str, Any]) -> list[str]:
    """Раздел отчёта об остатках удалённых программ (пустой, если их нет)."""
    count = int(leftovers.get("quarantined_count", 0) or 0)
    if not count:
        return []
    size = _format_bytes(int(leftovers.get("quarantined_size_bytes", 0) or 0))
    lines = [
        "",
        f"### Остатки удалённых программ и установщиков: {count} ({size})",
        "Перемещены в карантин, а не удалены — вернуть можно в течение 30 дней:",
        f"`{leftovers.get('batch_dir', '')}`",
    ]
    lines += [f"- {item.get('original', '')}" for item in leftovers.get("items", [])[:10]]
    if count > 10:
        lines.append(f"- … и ещё {count - 10}")
    return lines


def _format_leftovers_for_prompt(leftovers: dict[str, Any]) -> str:
    count = int(leftovers.get("quarantined_count", 0) or 0)
    if not count:
        return ""
    size = _format_bytes(int(leftovers.get("quarantined_size_bytes", 0) or 0))
    # Модели уходят только имена папок: полный путь содержит имя учётной записи.
    names = "; ".join(
        PureWindowsPath(item.get("original", "")).name for item in leftovers.get("items", [])[:5]
    )
    return (
        f"- Остатки удалённых программ перемещены в карантин (НЕ удалены, хранятся 30 дней): "
        f"{count} каталогов, {size}, папка карантина %LOCALAPPDATA%\\WinSpectorPro\\Quarantine; "
        f"например: {names}\n"
    )


def _empty_app_dirs_lines(empty_folders: dict[str, Any]) -> list[str]:
    """Раздел отчёта о пустых каталогах удалённых программ."""
    removed = empty_folders.get("app_dirs_removed") or []
    if not removed:
        return []
    lines = ["", f"### Пустые папки удалённых программ: {len(removed)}"]
    lines += [f"- {path}" for path in removed[:10]]
    if len(removed) > 10:
        lines.append(f"- … и ещё {len(removed) - 10}")
    return lines


def _format_empty_app_dirs_for_prompt(empty_folders: dict[str, Any]) -> str:
    removed = empty_folders.get("app_dirs_removed") or []
    if not removed:
        return ""
    # Только имена: путь в профиле содержит имя учётной записи.
    names = "; ".join(PureWindowsPath(path).name for path in removed[:5])
    return f"- Из них пустые папки удалённых программ: {len(removed)}; например: {names}\n"


_ABSOLUTE_PATH = re.compile(r"^(?:[A-Za-z]:\\|\\\\)")


def redact_for_ai(value: Any) -> Any:
    """
    Копия данных сессии без полных путей — модели уходят только имена папок.

    Полный путь в профиле содержит имя учётной записи. Значение-путь целиком
    заменяется последним компонентом, а путь профиля внутри текста (например,
    в сообщении об ошибке) — на `%USERPROFILE%`.
    """
    if isinstance(value, dict):
        return {key: redact_for_ai(item) for key, item in value.items()}
    if isinstance(value, list | tuple | set):
        return [redact_for_ai(item) for item in value]
    if not isinstance(value, str):
        return value
    if _ABSOLUTE_PATH.match(value):
        return PureWindowsPath(value).name or value[:3]
    profile = os.environ.get("USERPROFILE")
    if profile:
        value = re.sub(re.escape(profile), "%USERPROFILE%", value, flags=re.IGNORECASE)
    return value


def _format_skipped_categories(skipped: dict[str, Any]) -> str:
    """Строка для промпта: какие категории отложены и почему."""
    if not skipped:
        return ""
    items = "; ".join(f"{category} — {reason}" for category, reason in sorted(skipped.items()))
    return f"- Отложено до закрытия программ: {items}\n"


def _format_bytes(value: int, language: str = "ru") -> str:
    """Человекочитаемый размер: 1536 -> '1.5 КБ'."""
    if value <= 0:
        return "0 bytes" if language == "en" else "0 байт"
    units = ("GB", "MB", "KB") if language == "en" else ("ГБ", "МБ", "КБ")
    for unit, threshold in zip(units, (1024**3, 1024**2, 1024), strict=True):
        if value >= threshold:
            return (
                f"{value / threshold:.2f} {unit}"
                if threshold == 1024**3
                else (f"{value / threshold:.1f} {unit}")
            )
    return f"{value} {'bytes' if language == 'en' else 'байт'}"


class AICommunicator(AIBase):
    """Определяет профиль пользователя и формирует итоговый отчёт."""

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
                thinking_level="low",
                max_output_tokens=4096,
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
        language: str = "ru",
    ) -> str:
        debloat = summary.get("debloat") or {}
        cleanup = summary.get("cleanup") or {}
        empty_folders = summary.get("empty_folders") or {}

        completed = debloat.get("completed") or []
        failed = debloat.get("failed") or []
        cleaned_bytes = int(cleanup.get("cleaned_size_bytes", 0) or 0)
        deleted_files = int(cleanup.get("deleted_files_count", 0) or 0)
        deleted_folders = int(empty_folders.get("deleted_folders_count", 0) or 0) + int(
            cleanup.get("deleted_folders_count", 0) or 0
        )
        skipped_files = int(cleanup.get("skipped_files_count", 0) or 0)
        skipped_bytes = int(cleanup.get("skipped_size_bytes", 0) or 0)
        skipped_categories = cleanup.get("skipped_categories") or {}
        leftovers = summary.get("leftovers") or {}
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

        if language == "en":
            return f"""
Write a short report in English Markdown for the user of a Windows optimizer.
Translate any Russian action descriptions into English. Do not include Russian text in the report.
USER PROFILES: {json.dumps(profiles)}
METRICS
- Freed space: {_format_bytes(cleaned_bytes, "en")} ({deleted_files} files)
- Components changed: {total_actions}
- Failed changes: {len(failed)}
- Empty folders removed: {deleted_folders}
- Skipped files (in use or newer than 24 hours): {skipped_files} ({_format_bytes(skipped_bytes, "en")})
- Skipped categories: {json.dumps(skipped_categories, ensure_ascii=False)}
- Uninstalled program leftovers moved to quarantine for 30 days, not deleted: {json.dumps(redact_for_ai(leftovers), ensure_ascii=False, default=str)}
ACTIONS PERFORMED
{actions_text}
REQUIREMENTS
1. Start with a short headline and metrics block.
2. Report ONLY the freed space above as freed; skipped files were NOT deleted.
3. List the actions performed, translating Russian descriptions into English.
4. If categories were skipped because a program was running, say so.
5. If leftovers were moved to quarantine, explain that they can be restored for 30 days.
6. Close by mentioning that a restore point was created before any changes.
7. Do not invent numbers or actions. Keep it under 240 words.
""".strip()

        return f"""
Write a short report in Russian Markdown for the user of a Windows optimizer.

USER PROFILES: {json.dumps(profiles)}
TONE: {tone}
SUGGESTED HEADLINE: {headline}

METRICS
- Освобождено места: {_format_bytes(cleaned_bytes)} ({deleted_files} файлов)
- Изменено компонентов: {total_actions}
- Не удалось изменить: {len(failed)}
- Удалено пустых папок: {deleted_folders}
{_format_empty_app_dirs_for_prompt(empty_folders)}- Пропущено файлов (заняты программами или созданы за последние сутки): {skipped_files} ({_format_bytes(skipped_bytes)})
{_format_skipped_categories(skipped_categories)}{_format_leftovers_for_prompt(leftovers)}
ACTIONS PERFORMED
{actions_text}

REQUIREMENTS
1. Start with the headline.
2. Add a short metrics block. Report ONLY the freed space above as freed;
   skipped files were NOT deleted — mention them honestly as skipped.
3. Add a "Что было сделано:" section listing the actions above.
4. If some categories were skipped because a program was running, say so
   in one sentence and suggest closing that program next time.
5. If leftovers of uninstalled programs were moved to quarantine, say they
   were MOVED (not deleted), can be restored for 30 days, and name the folder.
6. Close with one reassuring sentence mentioning that a restore point was
   created before any changes.
7. Do not invent numbers or actions that are not listed above.
8. Keep it under 240 words.
""".strip()

    async def generate_final_report(
        self,
        summary: dict[str, Any],
        plan: list[dict[str, Any]],
        profiles: list[str],
        language: str = "ru",
    ) -> str:
        """Формирует отчёт. При сбое ИИ возвращает локальный текстовый отчёт."""
        prompt = self._create_report_prompt(summary, plan, profiles, language=language)
        try:
            return await self._generate(
                prompt,
                context="generate_final_report",
                temperature=0.6,
                thinking_level="low",
                max_output_tokens=8192,
                use_cache=False,
            )
        except AIError as exc:
            logger.error("Не удалось сгенерировать отчёт через ИИ: %s", exc)
            return self.build_offline_report(summary, language=language)

    @staticmethod
    def build_offline_report(summary: dict[str, Any], language: str = "ru") -> str:
        """
        Резервный отчёт без обращения к ИИ.

        Оптимизация уже выполнена — пользователь обязан увидеть результат,
        даже если сеть недоступна.
        """
        debloat = summary.get("debloat") or {}
        cleanup = summary.get("cleanup") or {}
        empty_folders = summary.get("empty_folders") or {}
        completed = debloat.get("completed") or []

        cleaned_bytes = int(cleanup.get("cleaned_size_bytes", 0) or 0)
        deleted_files = int(cleanup.get("deleted_files_count", 0) or 0)
        deleted_folders = int(empty_folders.get("deleted_folders_count", 0) or 0) + int(
            cleanup.get("deleted_folders_count", 0) or 0
        )
        skipped_files = int(cleanup.get("skipped_files_count", 0) or 0)
        skipped_bytes = int(cleanup.get("skipped_size_bytes", 0) or 0)
        skipped_categories = cleanup.get("skipped_categories") or {}
        leftovers = summary.get("leftovers") or {}

        if language == "en":
            lines = [
                "## ✅ Optimization complete",
                "",
                f"- **Freed space:** {_format_bytes(cleaned_bytes, 'en')} ({deleted_files} files)",
                f"- **Components changed:** {len(completed)}",
                f"- **Empty folders removed:** {deleted_folders}",
            ]
            if skipped_files:
                lines.append(
                    f"- **Skipped:** {skipped_files} files ({_format_bytes(skipped_bytes, 'en')}) — "
                    "in use or created in the last 24 hours"
                )
            if skipped_categories:
                lines += ["", "### Deferred until programs are closed:"]
                lines += [f"- {category}" for category in sorted(skipped_categories)]
            leftover_count = int(leftovers.get("quarantined_count", 0) or 0)
            if leftover_count:
                lines += [
                    "",
                    f"### Program leftovers moved to quarantine: {leftover_count}",
                    "These files were moved, not deleted, and can be restored for 30 days.",
                    f"`{leftovers.get('batch_dir', '')}`",
                ]
                lines += [
                    f"- {item.get('original', '')}" for item in leftovers.get("items", [])[:10]
                ]
            removed = empty_folders.get("app_dirs_removed") or []
            if removed:
                lines += ["", f"### Empty program folders removed: {len(removed)}"]
                lines += [f"- {path}" for path in removed[:10]]
            if completed:
                lines += ["", "### Changes made:"]
                lines += [
                    f"- {item.get('action', 'Changed')} {item.get('id', 'component')}"
                    for item in completed
                    if isinstance(item, dict)
                ]
            lines += [
                "",
                "_A system restore point was created before changes._",
                "",
                "> This report was generated locally because the AI service was unavailable.",
            ]
            return "\n".join(lines)

        lines = [
            "## ✅ Оптимизация завершена",
            "",
            f"- **Освобождено места:** {_format_bytes(cleaned_bytes)} ({deleted_files} файлов)",
            f"- **Изменено компонентов:** {len(completed)}",
            f"- **Удалено пустых папок:** {deleted_folders}",
        ]
        if skipped_files:
            lines.append(
                f"- **Пропущено:** {skipped_files} файлов ({_format_bytes(skipped_bytes)}) — "
                "заняты программами или созданы за последние сутки"
            )
        if skipped_categories:
            lines += ["", "### Отложено до закрытия программ:"]
            lines += [
                f"- {category}: {reason}" for category, reason in sorted(skipped_categories.items())
            ]
        lines += _leftovers_lines(leftovers)
        lines += _empty_app_dirs_lines(empty_folders)

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
{json.dumps(redact_for_ai(kwargs), indent=1, ensure_ascii=False, default=str)}

Respond in Russian Markdown.
""".strip()
        return await self._generate(
            prompt,
            context="ai_suggestions",
            temperature=0.8,
            thinking_level="low",
            max_output_tokens=8192,
            use_cache=False,
        )
