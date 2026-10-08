# src/winspector/core/modules/smart_cleaner.py
"""
Поиск и удаление системного мусора.

Важное архитектурное правило: **пути к удаляемым файлам никогда не приходят
от ИИ**. Сканер находит их сам по правилам из базы знаний, а модель лишь
отвечает «чистить эту категорию или нет». Даже полностью скомпрометированный
ответ модели не может привести к удалению произвольного файла.

Порядок любой очистки один и тот же: сначала удаляются файлы, и только потом
проверяются и удаляются опустевшие каталоги (см. `cleanup_engine`).
"""

from __future__ import annotations

import asyncio
import fnmatch
import glob
import logging
import os
import subprocess
from collections.abc import Iterable
from datetime import datetime, timedelta
from pathlib import Path, PurePath
from typing import Any, ClassVar

import psutil

from . import cleanup_engine as engine
from . import leftover_scanner

logger = logging.getLogger(__name__)

# Флаг, скрывающий консольные окна при запуске внешних команд из GUI.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# Файл во временном каталоге моложе этого порога считается рабочим файлом
# запущенной программы и не трогается. Столько же выжидает CCleaner.
TEMP_MIN_AGE_HOURS = 24

# Журналы и дампы моложе недели остаются: по ним разбирают свежие сбои.
DIAGNOSTICS_MIN_AGE_HOURS = 168

# Кеш шейдеров, в который не писали месяц, принадлежит заброшенной игре.
SHADER_MIN_AGE_HOURS = 720

# Папки с данными пользователя: их содержимое не может быть «мусором» ни по
# какому правилу — ни из базы знаний, ни по решению ИИ.
USER_DATA_FOLDERS = (
    "Desktop",
    "Documents",
    "Downloads",
    "Pictures",
    "Videos",
    "Music",
    "Favorites",
    "Contacts",
    "Links",
    "Saved Games",
    "Searches",
    "OneDrive",
    "source",
)


def _human_size(value: int) -> str:
    for unit, threshold in (("ГБ", 1024**3), ("МБ", 1024**2), ("КБ", 1024)):
        if value >= threshold:
            return f"{value / threshold:.1f} {unit}"
    return f"{value} Б"


class CleanupSummary(dict):
    """
    Итог очистки.

    Считается только то, что действительно удалено; всё, что пропущено,
    учитывается отдельно с причиной — отчёт не должен приписывать себе
    байты, которые остались на диске.
    """

    COUNTERS = (
        "cleaned_size_bytes",
        "deleted_files_count",
        "deleted_folders_count",
        "skipped_files_count",
        "skipped_size_bytes",
        "errors",
    )

    def __init__(self) -> None:
        super().__init__(dict.fromkeys(self.COUNTERS, 0))
        # Категории, пропущенные целиком, с причиной (например, запущен браузер).
        self["skipped_categories"] = {}
        # То же для окна отчёта: какая программа мешала и сколько ждёт очистки.
        self["deferred"] = []

    def add(
        self,
        size: int = 0,
        count: int = 0,
        errors: int = 0,
        *,
        folders: int = 0,
        skipped: int = 0,
        skipped_size: int = 0,
    ) -> None:
        self["cleaned_size_bytes"] += size
        self["deleted_files_count"] += count
        self["deleted_folders_count"] += folders
        self["skipped_files_count"] += skipped
        self["skipped_size_bytes"] += skipped_size
        self["errors"] += errors

    def add_tree(self, result: engine.TreeResult) -> None:
        """Учитывает итог обхода одного каталога."""
        self.add(
            size=result.sizes[engine.STATUS_DELETED],
            count=result.counts[engine.STATUS_DELETED],
            errors=result.errors,
            folders=result.dirs_removed,
            skipped=result.skipped_files,
            skipped_size=result.skipped_bytes,
        )

    def add_file(self, record: engine.FileRecord) -> None:
        """Учитывает вердикт по одному файлу."""
        if record.status == engine.STATUS_DELETED:
            self.add(size=record.size, count=1)
        elif record.status == engine.STATUS_ERROR:
            self.add(errors=1)
        else:
            self.add(skipped=1, skipped_size=record.size)

    def skip_category(self, category_id: str, reason: str) -> None:
        self["skipped_categories"][category_id] = reason

    def defer(self, *, processes: Iterable[str] = (), folder: str = "", size: int = 0) -> None:
        """Запоминает, что очистку отложили из-за работающей программы."""
        self["deferred"].append(
            {"processes": sorted(processes), "folder": folder, "size_bytes": int(size)}
        )


def _app_folder(path: Path) -> str:
    r"""
    Папка программы, которой принадлежит кеш.

    Для путей вида `%APPDATA%\Программа\Cache\Cache_Data` это первая папка
    под %APPDATA% или %LOCALAPPDATA%, а не ближайшая `Cache`.
    """
    for variable in ("APPDATA", "LOCALAPPDATA"):
        root = os.environ.get(variable)
        if not root:
            continue
        try:
            parts = path.relative_to(root).parts
        except ValueError:
            continue
        if parts:
            return parts[0]
    parent = path.parent
    return parent.parent.name if parent.name.lower() == "cache" else parent.name


class SmartCleaner:
    """Выполняет стандартную и «интеллектуальную» очистку системы."""

    PROTECTED_EXTENSIONS = frozenset(
        {".exe", ".dll", ".sys", ".py", ".ps1", ".bat", ".cmd", ".jar", ".msi", ".lnk"}
    )
    IGNORED_FILES_ON_EMPTY_CHECK = frozenset({"thumbs.db", "desktop.ini", ".ds_store"})
    PROTECTED_FOLDER_NAMES = frozenset(
        {
            "accountpictures",
            "administrative tools",
            "application shortcuts",
            "burn",
            "cd burning",
            "cookies",
            "credentials",
            "cryptneturlcache",
            "deviceids",
            "dpapimasterkeys",
            "en-us",
            "ru-ru",
            "sendto",
            "start menu",
            "startup",
            "templates",
            "windows",
            "system32",
            "programs",
            "recent",
            "libraries",
            "network shortcuts",
            "printer shortcuts",
            "quick launch",
            "user pinned",
        }
    )

    def __init__(self, cleanup_rules: list[dict[str, Any]]) -> None:
        logger.info("Инициализация SmartCleaner...")
        self.rules: dict[str, dict[str, Any]] = {
            str(rule["category_id"]): rule
            for rule in (cleanup_rules or [])
            if isinstance(rule, dict) and rule.get("category_id")
        }
        self._protected_roots = self._build_protected_roots()
        self._protected_subtrees = self._build_protected_subtrees()

    # --- Защита путей -----------------------------------------------------

    @staticmethod
    def _resolve_all(candidates: Iterable[str | Path]) -> set[Path]:
        resolved: set[Path] = set()
        for candidate in candidates:
            if not candidate:
                continue
            try:
                resolved.add(Path(candidate).resolve())
            except OSError:
                continue
        return resolved

    @classmethod
    def _build_protected_roots(cls) -> set[Path]:
        """Каталоги, содержимое которых нельзя удалять целиком."""
        system_root = os.environ.get("SystemRoot", r"C:\Windows")
        return cls._resolve_all(
            [
                system_root,
                str(Path(system_root) / "System32"),
                os.environ.get("ProgramFiles", r"C:\Program Files"),
                os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                os.environ.get("ProgramData", r"C:\ProgramData"),
                os.environ.get("USERPROFILE", ""),
                os.environ.get("APPDATA", ""),
                os.environ.get("LOCALAPPDATA", ""),
            ]
        )

    @classmethod
    def _build_protected_subtrees(cls) -> set[Path]:
        """
        Каталоги, внутрь которых очистка не заходит вовсе.

        В отличие от `_protected_roots`, защищён не только сам каталог, но и
        всё его содержимое: документы пользователя, закреплённые списки
        переходов, карантин Defender, кеш пакетов установщиков — то, что
        нельзя восстановить перезапуском программы.
        """
        profile = os.environ.get("USERPROFILE", "")
        public = os.environ.get("PUBLIC", "")
        appdata = os.environ.get("APPDATA", "")
        program_data = os.environ.get("ProgramData", r"C:\ProgramData")
        system_root = os.environ.get("SystemRoot", r"C:\Windows")

        candidates: list[Path] = []
        if profile:
            candidates += [Path(profile) / name for name in USER_DATA_FOLDERS]
        if public:
            candidates += [Path(public) / name for name in USER_DATA_FOLDERS[:6]]
        if appdata:
            shell = Path(appdata) / "Microsoft" / "Windows"
            # Recent: списки переходов и закреплённые в них элементы.
            candidates += [shell / "Recent", shell / "Start Menu"]
            # IRC- и мессенджер-клиенты хранят в `logs` историю переписки,
            # а не диагностику: это данные пользователя, их не вернуть.
            candidates += [
                Path(appdata) / "HexChat" / "logs",
                Path(appdata) / "mIRC" / "logs",
                Path(appdata) / ".purple" / "logs",
                Path(appdata) / "Miranda NG",
                Path(appdata) / "Thunderbird",
            ]
        candidates += [
            Path(program_data) / "Microsoft" / "Windows Defender" / "Quarantine",
            # Кеш пакетов Store и установщика Visual Studio: без них
            # приложения не чинятся и не обновляются.
            Path(program_data) / "Microsoft" / "Windows" / "AppRepository",
            Path(program_data) / "Microsoft" / "VisualStudio" / "Packages",
            Path(system_root) / "System32" / "config",
            Path(system_root) / "WinSxS",
            # Журналы автологгеров ETW: без них ломается служба журнала событий.
            Path(system_root) / "System32" / "LogFiles" / "WMI" / "RtBackup",
        ]
        return cls._resolve_all(candidates)

    def is_safe_to_delete(self, path: Path) -> bool:
        """
        Проверяет, что путь допустимо удалять.

        Отсекает корни дисков, системные каталоги, любые пути, являющиеся
        предками защищённых директорий, и всё внутри защищённых поддеревьев.

        Каталоги верхнего уровня специально не запрещены целиком: под запрет
        попадали бы легитимные `C:\\$Recycle.Bin` и `C:\\AMD` из базы знаний.
        Системные каталоги того же уровня отсекает явный список.
        """
        try:
            resolved = Path(path).resolve()
        except (OSError, ValueError):
            return False

        # Корень диска: C:\ или \\server\share.
        if resolved.parent == resolved or len(resolved.parts) <= 1:
            return False

        if resolved in self._protected_roots:
            return False

        # Путь не должен быть родителем защищённого каталога: удаление
        # `C:\Users` снесло бы профиль пользователя вместе со всем содержимым.
        if any(root != resolved and resolved in root.parents for root in self._protected_roots):
            return False

        parents = set(resolved.parents)
        return not any(
            resolved == subtree or subtree in parents or resolved in subtree.parents
            for subtree in self._protected_subtrees
        )

    # --- Правила ------------------------------------------------------------

    @staticmethod
    def _min_age_seconds(rule: dict[str, Any] | None) -> float | None:
        """Порог свежести из правила: `min_age_hours` либо `age_days`."""
        if not rule:
            return None
        hours = rule.get("min_age_hours")
        if hours:
            return float(hours) * 3600
        days = rule.get("age_days")
        if days:
            return float(days) * 86400
        return None

    @staticmethod
    def _running_process_names() -> set[str]:
        names: set[str] = set()
        for process in psutil.process_iter(["name"]):
            name = process.info.get("name")
            if name:
                names.add(name.lower())
        return names

    # --- Поиск мусора -----------------------------------------------------

    async def find_junk_files_deep(self, safety_levels: set[str] | None = None) -> dict[str, Any]:
        """
        Сканирует категории из базы знаний и возвращает найденное.

        Args:
            safety_levels: сканировать только правила с этими уровнями. Без ИИ
                очищаются лишь категории `high`, и обходить ради отчёта кеш
                uv на полмиллиона файлов (полминуты диска) незачем.
        """
        rules = {
            cid: rule
            for cid, rule in self.rules.items()
            if safety_levels is None or str(rule.get("safety", "medium")).lower() in safety_levels
        }
        logger.info("Начало поиска ненужных файлов по %d правилам.", len(rules))

        results = await asyncio.gather(
            *(self._scan_rule(cid, rule) for cid, rule in rules.items()),
            return_exceptions=True,
        )

        junk_summary: dict[str, Any] = {}
        claimed: list[str] = []
        for result in results:
            if isinstance(result, BaseException):
                logger.error("Ошибка при сканировании категории: %s", result)
                continue
            if result:
                self._drop_claimed_folders(result, claimed)
            if result and result.get("total_size", 0) > 0:
                junk_summary[result["category_id"]] = result

        logger.info("Поиск завершён: найдено %d категорий мусора.", len(junk_summary))
        return junk_summary

    @staticmethod
    def _drop_claimed_folders(result: dict[str, Any], claimed: list[str]) -> None:
        """
        Каталог принадлежит первой категории, которая его нашла.

        Правила перекрываются: общее правило для кешей Chromium находит и
        `Claude\\Code Cache`, и кеши внутри `%TEMP%`. Без этой проверки размер
        считался бы дважды, а главное — вложенный путь обходил бы ограничения
        своего «родителя» (порог свежести и целостность подкаталогов %TEMP%).
        """
        sizes: dict[str, int] = result.pop("_folder_sizes", {})
        kept: list[str] = []
        for folder in result.get("folders_to_clean") or []:
            key = folder.lower().rstrip("\\")
            if any(key == c or key.startswith(c + "\\") for c in claimed):
                result["total_size"] -= sizes.get(folder, 0)
                continue
            kept.append(folder)
            claimed.append(key)
        result["folders_to_clean"] = kept
        result["found_items_count"] = len(kept) + len(result.get("files_to_delete") or [])

    @staticmethod
    def _expand_rule_paths(rule: dict[str, Any]) -> list[tuple[str, bool]]:
        """
        Разворачивает пути правила в пары (путь, это_маска_файлов).

        В правилах для каталогов (`cleanup_type: folder`, по умолчанию) `*`
        означает подстановку каталога: `User Data\\*\\Cache` — кеш каждого
        профиля браузера, `*\\Saved\\Logs` — журналы каждой игры на Unreal.
        В правилах для файлов (`cleanup_type: files`) `*` — маска имени файла.
        Ссылки и junction-точки среди совпадений отбрасываются: корень обхода
        обязан быть настоящим каталогом.
        """
        expanded: list[tuple[str, bool]] = []
        as_files = str(rule.get("cleanup_type", "folder")).lower() == "files"
        excluded = [
            os.path.expandvars(str(p)).lower().rstrip("\\") + "\\"
            for p in rule.get("exclude_under") or []
        ]
        for raw in rule.get("paths") or []:
            path = os.path.expandvars(str(raw))
            if "*" not in path:
                expanded.append((path, False))
            elif as_files:
                expanded.append((path, True))
            else:
                # `glob.glob`, а не `Path.glob`: шаблон приходит строкой с
                # переменными окружения и абсолютным корнем.
                for match in sorted(glob.glob(path)):  # noqa: PTH207
                    if any(match.lower().startswith(prefix) for prefix in excluded):
                        continue
                    candidate = Path(match)
                    try:
                        if candidate.is_symlink() or candidate.is_junction():
                            continue
                        if candidate.is_dir():
                            expanded.append((match, False))
                    except OSError:
                        continue
        return expanded

    async def _scan_rule(self, category_id: str, rule: dict[str, Any]) -> dict[str, Any]:
        """Сканирует пути одного правила: маски ищут файлы, каталоги — размер."""
        # Раскрытие шаблонов обходит каталоги — не в потоке цикла событий.
        expanded = await asyncio.to_thread(self._expand_rule_paths, rule)
        paths = [path for path, _ in expanded]
        min_age = self._min_age_seconds(rule)
        atomic = bool(rule.get("atomic_subdirs"))

        tasks = [
            asyncio.to_thread(self._find_files_by_mask, path, rule)
            if is_mask
            else self._calculate_dir_size_safe(Path(path), min_age, atomic)
            for path, is_mask in expanded
        ]
        scan_results = await asyncio.gather(*tasks, return_exceptions=True)

        total_size = 0
        files_to_delete: list[str] = []
        folders_to_clean: list[str] = []
        folder_sizes: dict[str, int] = {}

        for path, result in zip(paths, scan_results, strict=True):
            if isinstance(result, BaseException):
                logger.debug("Не удалось просканировать '%s': %s", path, result)
                continue
            if isinstance(result, list):
                for file_path, file_size in result:
                    total_size += file_size
                    files_to_delete.append(str(file_path))
            elif isinstance(result, int) and result > 0:
                if self.is_safe_to_delete(Path(path)):
                    total_size += result
                    folders_to_clean.append(path)
                    folder_sizes[path] = result
                else:
                    logger.warning(
                        "Путь '%s' из правила '%s' отклонён защитой путей.",
                        path,
                        category_id,
                    )

        # В отчёт для ИИ уходят только смысловые поля: `provenance` и прочая
        # служебная информация лишь раздувают промпт.
        return {
            "category_id": category_id,
            "description_ru": rule.get("description_ru", ""),
            "safety": rule.get("safety", "medium"),
            "total_size": total_size,
            "found_items_count": len(files_to_delete) + len(folders_to_clean),
            "files_to_delete": files_to_delete,
            "folders_to_clean": folders_to_clean,
            "_folder_sizes": folder_sizes,
        }

    @staticmethod
    def _mask_base_dir(pattern: Path) -> Path:
        """
        Возвращает реальный каталог, с которого начинается обход.

        Для `C:\\Temp\\**\\*.log` это `C:\\Temp`: брать `pattern.parent`
        нельзя — он указывал бы на несуществующий каталог `**`.
        """
        parts = pattern.parts
        for index, part in enumerate(parts):
            if any(char in part for char in "*?["):
                return Path(*parts[:index]) if index else Path()
        return pattern.parent

    def _find_files_by_mask(
        self, path_with_mask: str, rule: dict[str, Any]
    ) -> list[tuple[Path, int]]:
        """
        Ищет файлы по маске.

        Маска с `**` включает рекурсивный обход, иначе просматривается только
        сам каталог — это исключает случайный спуск по всему диску.
        """
        pattern = Path(path_with_mask)
        mask = pattern.name
        recursive = "**" in path_with_mask
        base_dir = self._mask_base_dir(pattern)
        age_days = rule.get("age_days")
        cutoff = datetime.now() - timedelta(days=age_days) if age_days else None
        # Файлы не больше этого размера не трогаются: пустую базу эскизов
        # проводник тут же создаёт заново, и «освобождённое» место вернулось бы.
        min_bytes = int(rule.get("min_file_bytes") or 0)

        found: list[tuple[Path, int]] = []
        if not base_dir.is_dir():
            return found

        cutoff_ts = cutoff.timestamp() if cutoff else None
        protected = self.PROTECTED_EXTENSIONS
        stack = [str(base_dir)]

        # `os.scandir` отдаёт размер и время изменения вместе с именем файла,
        # поэтому проверки возраста и размера не стоят отдельных обращений
        # к диску — в отличие от связки `os.walk` + `Path.stat()`.
        while stack:
            current = stack.pop()
            try:
                with os.scandir(current) as entries:
                    for entry in entries:
                        try:
                            # Ссылки и junction-точки не раскрываются: за ними
                            # может оказаться каталог вне зоны очистки.
                            if entry.is_symlink() or entry.is_junction():
                                continue
                            if entry.is_dir(follow_symlinks=False):
                                if recursive:
                                    stack.append(entry.path)
                                continue
                            if not entry.is_file(follow_symlinks=False):
                                continue
                            if not fnmatch.fnmatch(entry.name, mask):
                                continue
                            if PurePath(entry.name).suffix.lower() in protected:
                                continue

                            stat = entry.stat(follow_symlinks=False)
                            if cutoff_ts is not None and stat.st_mtime > cutoff_ts:
                                continue
                            if stat.st_size <= min_bytes:
                                continue
                            found.append((Path(entry.path), stat.st_size))
                        except OSError:
                            continue
            except OSError:
                continue

        return found

    # --- Стандартная очистка ---------------------------------------------

    # Код владеет этим планом целиком: сюда попадает только то, что Windows
    # сама предлагает удалить в «Очистке диска», либо кеши, которые программа
    # пересоздаёт при следующем запуске.
    STANDARD_PLAN: ClassVar[dict[str, dict[str, Any]]] = {
        "temp_files": {
            "type": "folder_content",
            "paths": [r"%WINDIR%\Temp", "%TEMP%"],
            "min_age_hours": TEMP_MIN_AGE_HOURS,
            # Подкаталог, которым сейчас пользуется программа, не трогается.
            "atomic_subdirs": True,
        },
        "windows_update_cache": {
            "type": "folder_content",
            "paths": [r"%WINDIR%\SoftwareDistribution\Download"],
            # Скачанное, но ещё не установленное обновление не перекачивается.
            "min_age_hours": TEMP_MIN_AGE_HOURS,
        },
        "windows_error_reports": {
            "type": "folder_content",
            "paths": [
                r"%PROGRAMDATA%\Microsoft\Windows\WER\ReportArchive",
                r"%PROGRAMDATA%\Microsoft\Windows\WER\ReportQueue",
                r"%PROGRAMDATA%\Microsoft\Windows\WER\Temp",
                r"%LOCALAPPDATA%\Microsoft\Windows\WER\ReportArchive",
                r"%LOCALAPPDATA%\Microsoft\Windows\WER\ReportQueue",
            ],
            "min_age_hours": DIAGNOSTICS_MIN_AGE_HOURS,
        },
        "user_crash_dumps": {
            "type": "folder_content",
            "paths": [r"%LOCALAPPDATA%\CrashDumps"],
            "min_age_hours": DIAGNOSTICS_MIN_AGE_HOURS,
        },
        # Дампы памяти нужны, чтобы разобрать свежий сбой: неделю они остаются.
        "memory_dumps": {
            "type": "files_by_mask",
            "paths": [
                r"%WINDIR%\MEMORY.DMP",
                r"%WINDIR%\Minidump\*.dmp",
                r"%WINDIR%\LiveKernelReports\*.dmp",
            ],
            "age_days": DIAGNOSTICS_MIN_AGE_HOURS // 24,
        },
        # Пустые базы эскизов и значков (до 1 МБ) не трогаются: проводник
        # сразу создаёт их заново, и каждый запуск «освобождал» бы одно и то же.
        "thumbnail_cache": {
            "type": "files_by_mask",
            "paths": [r"%LOCALAPPDATA%\Microsoft\Windows\Explorer\thumbcache_*.db"],
            "min_file_bytes": 1024 * 1024,
        },
        # Кеш значков пересобирается проводником; занятые им файлы пропускаются.
        "icon_cache": {
            "type": "files_by_mask",
            "paths": [r"%LOCALAPPDATA%\Microsoft\Windows\Explorer\iconcache_*.db"],
            "min_file_bytes": 1024 * 1024,
        },
        # Кеш шейдеров DirectX: пересобирается при следующем запуске игры,
        # тот же пункт есть в «Очистке диска» Windows. Удаляется только то, во
        # что не писали месяц: кеш игр, в которые играют, остаётся — иначе
        # первые запуски подтормаживали бы на перекомпиляции шейдеров.
        "directx_shader_cache": {
            "type": "folder_content",
            "paths": [r"%LOCALAPPDATA%\D3DSCache"],
            "min_age_hours": SHADER_MIN_AGE_HOURS,
        },
        "dns_cache": {"type": "command", "command": ["ipconfig", "/flushdns"]},
    }

    async def perform_standard_cleanup(self) -> dict[str, Any]:
        """Детерминированная очистка общеизвестного мусора Windows."""
        logger.info("Начало стандартной очистки.")
        summary = CleanupSummary()

        for category, details in self.STANDARD_PLAN.items():
            kind = details["type"]
            logger.debug("Стандартная очистка: %s", category)

            if kind == "folder_content":
                min_age = self._min_age_seconds(details)
                for raw_path in details["paths"]:
                    result = await asyncio.to_thread(
                        self._clean_directory_content,
                        Path(os.path.expandvars(raw_path)),
                        min_age,
                        bool(details.get("atomic_subdirs")),
                    )
                    summary.add_tree(result)

            elif kind == "files_by_mask":
                for raw_path in details["paths"]:
                    expanded = os.path.expandvars(raw_path)
                    if "*" in expanded:
                        files = await asyncio.to_thread(self._find_files_by_mask, expanded, details)
                    else:
                        files = self._single_file_if_old(Path(expanded), details)
                    for file_path, _ in files:
                        summary.add_file(await self._delete_single_file(file_path))

            elif kind == "command":
                summary.add(errors=await self._run_command(details["command"]))

        logger.info(
            "Стандартная очистка завершена: освобождено %.2f МБ, пропущено %d файлов, ошибок %d.",
            summary["cleaned_size_bytes"] / (1024 * 1024),
            summary["skipped_files_count"],
            summary["errors"],
        )
        return dict(summary)

    @staticmethod
    def _single_file_if_old(candidate: Path, details: dict[str, Any]) -> list[tuple[Path, int]]:
        """Одиночный файл плана (MEMORY.DMP) с тем же порогом возраста, что у масок."""
        try:
            stat = candidate.stat()
        except OSError:
            return []
        age_days = details.get("age_days")
        if age_days and stat.st_mtime > datetime.now().timestamp() - age_days * 86400:
            return []
        return [(candidate, stat.st_size)] if candidate.is_file() else []

    @staticmethod
    async def _run_command(command: list[str]) -> int:
        """Выполняет внешнюю команду. Возвращает 1 при ошибке, иначе 0."""
        try:
            await asyncio.to_thread(
                subprocess.run,
                command,
                shell=False,
                check=True,
                capture_output=True,
                creationflags=_NO_WINDOW,
            )
            return 0
        except (OSError, subprocess.SubprocessError) as exc:
            logger.warning("Команда %s завершилась с ошибкой: %s", command, exc)
            return 1

    # --- Интеллектуальная очистка ----------------------------------------

    async def perform_deep_cleanup(
        self,
        decisions: dict[str, Any] | None,
        junk_report: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Удаляет мусор в категориях, одобренных ИИ или офлайн-планировщиком.

        Args:
            decisions: решения вида ``{"category_id": {"clean": bool}}``.
            junk_report: результат `find_junk_files_deep` — **единственный**
                источник путей для удаления.
        """
        summary = CleanupSummary()
        decisions = decisions or {}
        junk_report = junk_report or {}

        approved = [cid for cid, d in decisions.items() if isinstance(d, dict) and d.get("clean")]
        if not approved:
            logger.info("Ни одна категория не одобрена для глубокой очистки.")
            return dict(summary)

        logger.info("Глубокая очистка: одобрено категорий — %d.", len(approved))
        running: set[str] | None = None
        touched_dirs: set[Path] = set()

        for category_id in approved:
            report = junk_report.get(category_id)
            if not report:
                # Категория одобрена, но сканер ничего не нашёл — пропускаем.
                logger.debug("Категория '%s' отсутствует в отчёте сканера.", category_id)
                continue

            rule = self.rules.get(category_id) or {}
            # Кеш работающей программы трогать нельзя: она держит его в
            # памяти и перезапишет либо испортит. Категория ждёт следующего
            # запуска, а в отчёте честно сказано, почему.
            blockers = rule.get("requires_closed") or []
            if blockers:
                if running is None:
                    running = await asyncio.to_thread(self._running_process_names)
                active = sorted(p for p in blockers if str(p).lower() in running)
                if active:
                    waiting = int(report.get("total_size", 0) or 0)
                    reason = f"запущено: {', '.join(active)}"
                    if waiting:
                        reason += f" — ждёт {_human_size(waiting)}"
                    logger.info("Категория '%s' пропущена — %s.", category_id, reason)
                    summary.skip_category(category_id, reason)
                    summary.defer(processes=active, size=waiting)
                    continue

            logger.info("Очистка категории '%s'.", category_id)
            min_age = self._min_age_seconds(rule)
            tasks: list[Any] = []

            for raw_path in report.get("files_to_delete") or []:
                path = Path(raw_path)
                if not self.is_safe_to_delete(path):
                    logger.warning("Пропуск небезопасного пути: %s", path)
                    summary.add(errors=1)
                    continue
                touched_dirs.add(path.parent)
                tasks.append(self._delete_single_file(path))

            atomic = bool(rule.get("atomic_subdirs"))
            for raw_path in report.get("folders_to_clean") or []:
                path = Path(raw_path)
                if not self.is_safe_to_delete(path):
                    logger.warning("Пропуск небезопасного каталога: %s", path)
                    summary.add(errors=1)
                    continue
                if rule.get("skip_if_busy"):
                    busy = await asyncio.to_thread(self._busy_file, path, rule)
                    if busy:
                        logger.info("Кеш '%s' пропущен: файл занят (%s).", path, busy)
                        folder = _app_folder(path)
                        summary.skip_category(
                            f"{category_id}: {folder}", "программа запущена — её кеш занят"
                        )
                        summary.defer(folder=folder)
                        continue
                tasks.append(
                    asyncio.to_thread(self._clean_directory_content, path, min_age, atomic)
                )

            for result in await asyncio.gather(*tasks, return_exceptions=True):
                if isinstance(result, BaseException):
                    logger.error("Ошибка очистки '%s': %s", category_id, result)
                    summary.add(errors=1)
                elif isinstance(result, engine.TreeResult):
                    summary.add_tree(result)
                elif isinstance(result, engine.FileRecord):
                    summary.add_file(result)

        if touched_dirs:
            deleted, errors = await self._prune_dirs(touched_dirs)
            summary.add(folders=deleted, errors=errors)

        logger.info(
            "Глубокая очистка завершена: освобождено %.2f МБ, пропущено %d файлов, ошибок %d.",
            summary["cleaned_size_bytes"] / (1024 * 1024),
            summary["skipped_files_count"],
            summary["errors"],
        )
        return dict(summary)

    async def _prune_dirs(self, dirs: Iterable[Path]) -> tuple[int, int]:
        """Удаляет опустевшие каталоги. Устойчиво к исключениям в задачах."""
        results = await asyncio.gather(
            *(asyncio.to_thread(self._cleanup_empty_dirs, d) for d in dirs),
            return_exceptions=True,
        )
        deleted = errors = 0
        for result in results:
            if isinstance(result, BaseException):
                errors += 1
            elif isinstance(result, tuple) and len(result) == 2:
                deleted += result[0]
                errors += result[1]
        return deleted, errors

    async def cleanup_all_empty_folders_async(
        self,
        extra_paths: list[str] | None = None,
        app_dirs: Iterable[str] | None = None,
    ) -> dict[str, Any]:
        """
        Удаляет пустые каталоги во временных директориях и пустые каталоги
        удалённых программ (`app_dirs`, найденные `leftover_scanner`).

        Выполняется последним шагом — после того, как удаление файлов уже
        прошло. Личные папки пользователя (Documents, Downloads) намеренно
        исключены: пустой каталог там часто создан человеком осознанно.
        """
        logger.info("Поиск пустых каталогов во временных директориях.")

        roots = {os.path.expandvars(p) for p in ("%TEMP%", r"%WINDIR%\Temp", *(extra_paths or []))}
        tasks = [
            asyncio.to_thread(self._process_empty_folder_cleanup, Path(root))
            for root in roots
            if root and Path(root).is_dir() and self.is_safe_to_delete(Path(root))
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        deleted = errors = 0
        for result in results:
            if isinstance(result, BaseException):
                logger.warning("Ошибка обхода каталога: %s", result)
                errors += 1
            elif isinstance(result, tuple) and len(result) == 2:
                deleted += result[0]
                errors += result[1]

        removed_apps: list[str] = []
        for app_dir in app_dirs or []:
            removed, failed = await asyncio.to_thread(self._remove_empty_app_dir, Path(app_dir))
            deleted += removed
            errors += failed
            if removed and not Path(app_dir).exists():
                removed_apps.append(app_dir)
        if removed_apps:
            logger.info("Удалены пустые каталоги программ: %s", ", ".join(removed_apps))

        logger.info("Удалено пустых каталогов: %d (ошибок: %d).", deleted, errors)
        return {
            "deleted_folders_count": deleted,
            "errors": errors,
            "app_dirs_removed": removed_apps,
        }

    # --- Низкоуровневые операции -----------------------------------------

    def _is_dir_effectively_empty(self, path: Path) -> bool:
        """
        Каталог пуст или содержит только служебные файлы вроде Thumbs.db.

        `os.scandir` вместо `iterdir()`: перечисление прерывается на первой
        же «настоящей» записи, а не читает весь каталог целиком.
        """
        ignored = self.IGNORED_FILES_ON_EMPTY_CHECK
        try:
            with os.scandir(path) as entries:
                return all(entry.name.lower() in ignored for entry in entries)
        except OSError:
            return False

    def _remove_effectively_empty_dir(self, path: Path) -> bool:
        """
        Удаляет каталог, в котором нет ничего, кроме служебных файлов.

        Сначала убираются сами служебные файлы, затем — `rmdir`: если за это
        время в каталоге появилось что-то ещё, система откажет, и каталог
        останется. `rmtree` здесь недопустим — он снёс бы и новое содержимое.
        """
        try:
            with os.scandir(path) as entries:
                leftovers = [entry.path for entry in entries]
            for leftover in leftovers:
                os.unlink(leftover)  # noqa: PTH108 — строки из scandir
            path.rmdir()
            return True
        except OSError as exc:
            logger.debug("Не удалось удалить пустой каталог '%s': %s", path, exc)
            return False

    def _iter_dirs_bottom_up(self, root: Path) -> list[Path]:
        """
        Подкаталоги `root` от самых глубоких к верхним — без самого корня.

        Собственный обход вместо `os.walk`: тот раскрывает junction-точки, и
        «пустой» каталог мог бы оказаться за пределами очищаемого дерева.
        """
        collected: list[Path] = []
        stack = [root]
        while stack:
            current = stack.pop()
            try:
                with os.scandir(current) as entries:
                    for entry in entries:
                        try:
                            if entry.is_symlink() or entry.is_junction():
                                continue
                            if entry.is_dir(follow_symlinks=False):
                                child = Path(entry.path)
                                collected.append(child)
                                stack.append(child)
                        except OSError:
                            continue
            except OSError:
                continue
        collected.reverse()
        return collected

    def _remove_empty_app_dir(self, path: Path) -> tuple[int, int]:
        """
        Удаляет пустой каталог программы вместе с пустыми подкаталогами.

        Дерево проверяется заново: если за время оптимизации в нём появился
        хоть один файл (программу ставят заново), каталог остаётся целиком.
        Удаление — только `rmdir`, который откажет на непустом каталоге.
        """
        if not self.is_safe_to_delete(path) or leftover_scanner.empty_tree(path) is None:
            return 0, 0
        deleted = 0
        for current in [*self._iter_dirs_bottom_up(path), path]:
            try:
                current.rmdir()
            except OSError as exc:
                logger.debug("Не удалось удалить пустой каталог '%s': %s", current, exc)
                return deleted, 1
            deleted += 1
        return deleted, 0

    def _process_empty_folder_cleanup(self, root_path: Path) -> tuple[int, int]:
        """
        Обходит дерево снизу вверх и удаляет пустые каталоги.

        Проверки идут от дешёвых к дорогим: имя, затем пустота, и только для
        пустых — `is_safe_to_delete` с его `resolve()`. Большинство каталогов
        не пусты, и до системного вызова дело не доходит.
        """
        deleted = errors = 0

        for current in self._iter_dirs_bottom_up(root_path):
            if current.name.lower() in self.PROTECTED_FOLDER_NAMES:
                continue
            if not self._is_dir_effectively_empty(current):
                continue
            if not self.is_safe_to_delete(current):
                continue
            if self._remove_effectively_empty_dir(current):
                deleted += 1
            else:
                errors += 1

        return deleted, errors

    async def _delete_single_file(self, file_path: Path) -> engine.FileRecord:
        """Удаляет файл и возвращает вердикт (удалён / занят / нет прав)."""
        return await asyncio.to_thread(self._delete_single_file_sync, file_path)

    @staticmethod
    def _delete_single_file_sync(file_path: Path) -> engine.FileRecord:
        record = engine.FileRecord(path=str(file_path), size=0, status=engine.STATUS_DELETED)
        try:
            record.size = file_path.stat().st_size
            file_path.unlink()
        except FileNotFoundError:
            # Файл уже удалён другим процессом — это не ошибка и не заслуга.
            record.size = 0
            record.status = engine.STATUS_TOO_RECENT
            record.detail = "файл уже отсутствует"
        except OSError as exc:
            record.status = engine._status_from_oserror(exc)
            record.detail = engine._describe(exc)
            if record.status == engine.STATUS_LOCKED:
                # Файл занят другим процессом. Для кешей это норма, поэтому
                # такое событие не засоряет журнал уровнем WARNING.
                logger.debug("Файл занят, пропуск: %s", file_path)
            else:
                logger.warning("Не удалось удалить '%s': %s", file_path, exc)
        return record

    @staticmethod
    def _busy_file(path: Path, rule: dict[str, Any]) -> str:
        """
        Занятый файл в каталоге кеша или в соседних каталогах того же профиля.

        Для программ, имя процесса которых заранее неизвестно (приложения на
        Electron и WebView2), занятый файл — надёжный признак того, что
        программа запущена: Chromium держит открытыми `GPUCache`, индекс
        `Cache_Data` и базы `Local Storage` всё время работы.
        """
        busy = engine.first_locked_file(path)
        if busy:
            return busy
        # Профиль Chromium — каталог, где лежит кеш; для `Cache\\Cache_Data`
        # это уровень выше `Cache`.
        profile = path.parent.parent if path.name.lower() == "cache_data" else path.parent
        for sibling in rule.get("busy_siblings") or []:
            busy = engine.first_locked_file(profile / sibling)
            if busy:
                return busy
        return ""

    def _clean_directory_content(
        self,
        path: Path,
        min_age_seconds: float | None = None,
        atomic_subdirs: bool = False,
    ) -> engine.TreeResult:
        """
        Удаляет содержимое каталога; сам каталог сохраняется.

        Сначала файлы, затем опустевшие каталоги — см. `cleanup_engine`.
        """
        if not path.is_dir() or not self.is_safe_to_delete(path):
            return engine.TreeResult(root=str(path), mode="apply")
        return engine.process_tree(
            path, mode="apply", min_age_seconds=min_age_seconds, atomic_subdirs=atomic_subdirs
        )

    def _cleanup_empty_dirs(self, path: Path) -> tuple[int, int]:
        """Удаляет пустой каталог и поднимается вверх, пока каталоги пусты."""
        deleted = errors = 0
        current = path

        while self.is_safe_to_delete(current) and current.is_dir():
            if current.name.lower() in self.PROTECTED_FOLDER_NAMES:
                break
            try:
                if any(current.iterdir()):
                    break
                current.rmdir()
                deleted += 1
                current = current.parent
            except OSError as exc:
                logger.debug("Не удалось удалить пустой каталог '%s': %s", current, exc)
                errors += 1
                break

        return deleted, errors

    @staticmethod
    def _get_dir_size_safe(
        path: Path, min_age_seconds: float | None = None, atomic_subdirs: bool = False
    ) -> int:
        """
        Суммарный размер файлов, которые подлежат удалению; ссылки и слишком
        свежие файлы не учитываются — итог совпадает с тем, что удалит
        `_clean_directory_content` (за вычетом занятых файлов).

        Обход тот же, что и при удалении (`cleanup_engine`): на Windows
        размер приходит вместе с перечислением каталога, поэтому отдельных
        обращений к диску на каждый файл нет.
        """
        return engine.process_tree(
            path, mode="scan", min_age_seconds=min_age_seconds, atomic_subdirs=atomic_subdirs
        ).freed_bytes

    async def _calculate_dir_size_safe(
        self, path: Path, min_age_seconds: float | None = None, atomic_subdirs: bool = False
    ) -> int:
        """Асинхронная обёртка над `_get_dir_size_safe`."""
        if not path.is_dir():
            return 0
        return await asyncio.to_thread(
            self._get_dir_size_safe, path, min_age_seconds, atomic_subdirs
        )
