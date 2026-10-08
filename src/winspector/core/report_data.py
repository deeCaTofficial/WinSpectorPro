"""
Итог оптимизации в виде данных для окна отчёта.

Markdown-отчёт годится для файла и буфера обмена, но в окне из него
получается сплошной список с внутренними именами правил
(`chromium_app_caches: Code`). Здесь итог раскладывается по полям: сколько
освобождено, что изменено, какие программы мешали очистке и сколько места
ждёт их закрытия — с понятными названиями программ.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Названия программ по имени процесса или папки в %APPDATA%: (русское, английское).
_APP_NAMES: dict[str, tuple[str, str]] = {
    "browser.exe": ("Яндекс Браузер", "Yandex Browser"),
    "chrome.exe": ("Google Chrome", "Google Chrome"),
    "cities2.exe": ("Cities: Skylines II", "Cities: Skylines II"),
    "claude.exe": ("Claude", "Claude"),
    "code.exe": ("Visual Studio Code", "Visual Studio Code"),
    "discord.exe": ("Discord", "Discord"),
    "epicgameslauncher.exe": ("Epic Games Launcher", "Epic Games Launcher"),
    "excel.exe": ("Microsoft Excel", "Microsoft Excel"),
    "figma.exe": ("Figma", "Figma"),
    "firefox.exe": ("Firefox", "Firefox"),
    "lghub.exe": ("Logitech G HUB", "Logitech G HUB"),
    "lghub_agent.exe": ("Logitech G HUB", "Logitech G HUB"),
    "lghub_updater.exe": ("Logitech G HUB", "Logitech G HUB"),
    "msedge.exe": ("Microsoft Edge", "Microsoft Edge"),
    "nvidia app.exe": ("NVIDIA App", "NVIDIA App"),
    "nvidia overlay.exe": ("NVIDIA App", "NVIDIA App"),
    "obs64.exe": ("OBS Studio", "OBS Studio"),
    "paradox launcher.exe": ("Paradox Launcher", "Paradox Launcher"),
    "powerpnt.exe": ("Microsoft PowerPoint", "Microsoft PowerPoint"),
    "steam.exe": ("Steam", "Steam"),
    "studio64.exe": ("Android Studio", "Android Studio"),
    "teamspeak.exe": ("TeamSpeak", "TeamSpeak"),
    "telegram.exe": ("Telegram", "Telegram"),
    "ts3client_win64.exe": ("TeamSpeak", "TeamSpeak"),
    "vrmonitor.exe": ("SteamVR", "SteamVR"),
    "vrserver.exe": ("SteamVR", "SteamVR"),
    "winword.exe": ("Microsoft Word", "Microsoft Word"),
    "yandex music.exe": ("Яндекс Музыка", "Yandex Music"),
    # Папки программ в %APPDATA%.
    "code": ("Visual Studio Code", "Visual Studio Code"),
    "yandexmusic": ("Яндекс Музыка", "Yandex Music"),
    "telegram desktop": ("Telegram", "Telegram"),
}


def app_name(raw: str, language: str = "ru") -> str:
    """Понятное название программы по имени процесса или папки."""
    known = _APP_NAMES.get(raw.strip().lower())
    if known:
        return known[1] if language == "en" else known[0]
    name = raw.strip()
    if name.lower().endswith(".exe"):
        name = name[:-4]
    # `discord`, `steam` — с заглавной; `launch-preview-static` оставляем как есть.
    if name.isalpha() and name.islower():
        name = name.capitalize()
    return name


@dataclass(frozen=True)
class DeferredApp:
    """Программа, из-за которой очистку отложили, и сколько места ждёт её закрытия."""

    name: str
    size_bytes: int = 0


@dataclass(frozen=True)
class ReportData:
    freed_bytes: int = 0
    deleted_files: int = 0
    deleted_folders: int = 0
    skipped_files: int = 0
    skipped_bytes: int = 0
    changes: list[str] = field(default_factory=list)
    failed_changes: int = 0
    deferred: list[DeferredApp] = field(default_factory=list)
    quarantined_count: int = 0
    quarantined_bytes: int = 0
    ai_used: bool = False
    # Ключ есть, но Gemini не ответил: отчёт и план собраны без ИИ вынужденно.
    ai_failed: bool = False
    # Текст отчёта Gemini; без ИИ — пусто: всё показано полями выше.
    ai_text: str = ""

    @property
    def deferred_bytes(self) -> int:
        return sum(app.size_bytes for app in self.deferred)

    @property
    def nothing_done(self) -> bool:
        return not (
            self.freed_bytes or self.deleted_folders or self.changes or self.quarantined_count
        )

    @classmethod
    def from_summary(
        cls,
        summary: dict[str, Any],
        *,
        language: str = "ru",
        ai_used: bool = False,
        ai_failed: bool = False,
        ai_text: str = "",
        display_names: dict[str, str] | None = None,
    ) -> ReportData:
        """
        `display_names` — названия служб из Windows по имени службы в нижнем
        регистре: в отчёте «Epic Games Updater», а не `epicgamesupdater`.
        """
        debloat = summary.get("debloat") or {}
        cleanup = summary.get("cleanup") or {}
        empty_folders = summary.get("empty_folders") or {}
        leftovers = summary.get("leftovers") or {}

        def number(source: dict[str, Any], key: str) -> int:
            return int(source.get(key, 0) or 0)

        changes = []
        for item in debloat.get("completed") or []:
            if not isinstance(item, dict):
                continue
            explanation = item.get("user_explanation_ru") if language != "en" else ""
            changes.append(explanation or describe_change(item, language, display_names))

        return cls(
            freed_bytes=number(cleanup, "cleaned_size_bytes"),
            deleted_files=number(cleanup, "deleted_files_count"),
            deleted_folders=number(cleanup, "deleted_folders_count")
            + number(empty_folders, "deleted_folders_count"),
            skipped_files=number(cleanup, "skipped_files_count"),
            skipped_bytes=number(cleanup, "skipped_size_bytes"),
            changes=changes,
            failed_changes=len(debloat.get("failed") or []),
            deferred=_group_deferred(cleanup.get("deferred") or [], language),
            quarantined_count=number(leftovers, "quarantined_count"),
            quarantined_bytes=number(leftovers, "quarantined_size_bytes"),
            ai_used=ai_used,
            ai_failed=ai_failed,
            ai_text=ai_text,
        )


# Что сделано с компонентом — когда пояснения нет или оно только по-русски.
_ACTION_TEXT: dict[str, tuple[str, str]] = {
    "disable": ("отключена", "disabled"),
    "set_manual": ("запускается по требованию", "set to start on demand"),
    "stop": ("остановлена", "stopped"),
}


def describe_change(
    item: dict[str, Any],
    language: str = "ru",
    display_names: dict[str, str] | None = None,
) -> str:
    """«Service “Epic Games Updater” disabled» вместо внутреннего «disable epicgamesupdater»."""
    target = str(item.get("id") or "?")
    target = (display_names or {}).get(target.lower()) or target
    action = str(item.get("action") or "")
    if item.get("type") == "uwp_app" or action == "remove":
        return f"App “{target}” removed" if language == "en" else f"Приложение «{target}» удалено"
    ru, en = _ACTION_TEXT.get(action, ("изменена", "changed"))
    return f"Service “{target}” {en}" if language == "en" else f"Служба «{target}» {ru}"


def _group_deferred(items: list[dict[str, Any]], language: str) -> list[DeferredApp]:
    """
    Одна строка на программу: Steam держит два кеша, VS Code — три.

    Крупные — первыми; программы без известного размера — в конце по алфавиту.
    """
    sizes: dict[str, int] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        sources = item.get("processes") or [item.get("folder") or ""]
        names = {app_name(source, language) for source in sources if source}
        if not names:
            continue
        # Если категорию держат несколько программ, размер относится к первой:
        # иначе он посчитался бы дважды.
        first, *rest = sorted(names)
        sizes[first] = sizes.get(first, 0) + int(item.get("size_bytes", 0) or 0)
        for name in rest:
            sizes.setdefault(name, 0)
    ordered = sorted(sizes.items(), key=lambda pair: (-pair[1], pair[0].lower()))
    return [DeferredApp(name, size) for name, size in ordered]


def format_size(value: int, language: str = "ru") -> str:
    """Размер для окна: «1,45 ГБ» / «1.45 GB»."""
    units = ("GB", "MB", "KB", "bytes") if language == "en" else ("ГБ", "МБ", "КБ", "байт")
    for unit, threshold, digits in zip(units[:3], (1024**3, 1024**2, 1024), (2, 1, 0), strict=True):
        if value >= threshold:
            text = f"{value / threshold:.{digits}f}"
            if language != "en":
                text = text.replace(".", ",")
            return f"{text} {unit}"
    return f"{max(value, 0)} {units[3]}"


def format_count(value: int, language: str = "ru") -> str:
    """Число с разделением разрядов: «9 747» / «9,747»."""
    text = f"{value:,}"
    return text if language == "en" else text.replace(",", " ")


def plural(value: int, one: str, few: str, many: str) -> str:
    """Русская форма слова для числа: 1 файл, 2 файла, 5 файлов."""
    value = abs(value) % 100
    if 11 <= value <= 14:
        return many
    value %= 10
    if value == 1:
        return one
    if 2 <= value <= 4:
        return few
    return many
