# scripts/audit_cleanup.py
"""
Сухой прогон очистки: что нашла бы программа, ничего не удаляя.

Повторяет офлайн-сценарий (стандартный план + категории базы знаний с
`safety: high`), но вместо удаления для каждого файла проверяет, *можно ли*
его удалить: занят ли он другим процессом, хватает ли прав. Для файлов без
прав выясняет владельца и права администраторов, для занятых — какой процесс
их держит. Итог печатается и сохраняется в Markdown.

Запуск:
    python scripts/audit_cleanup.py [--out ОТЧЁТ.md] [--no-medium]

Ничего в системе не меняет. Права администратора не нужны: отчёт отдельно
показывает, что изменилось бы при их наличии.
"""

from __future__ import annotations

import argparse
import asyncio
import ctypes
import os
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import yaml  # noqa: E402

from src.winspector.core.modules import cleanup_engine as engine  # noqa: E402
from src.winspector.core.modules import leftover_scanner  # noqa: E402
from src.winspector.core.modules.smart_cleaner import SmartCleaner  # noqa: E402
from src.winspector.core.offline_planner import SAFE_LEVEL  # noqa: E402

KB_PATH = PROJECT_ROOT / "src" / "winspector" / "data" / "knowledge_base" / "cleanup_rules.yaml"

LOCK_HOLDER_PROBES = 60  # сколько занятых файлов опросить через Restart Manager


def fmt_size(value: int) -> str:
    for unit, threshold in (("ГБ", 1024**3), ("МБ", 1024**2), ("КБ", 1024)):
        if value >= threshold:
            return f"{value / threshold:.1f} {unit}"
    return f"{value} Б"


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def load_rules() -> list[dict[str, Any]]:
    with KB_PATH.open(encoding="utf-8") as handle:
        return [r for r in yaml.safe_load(handle) if isinstance(r, dict)]


def collect_targets(cleaner: SmartCleaner, rules: list[dict[str, Any]], include_medium: bool):
    """
    Список целей ровно в том виде, в каком их обработает программа.

    Пути категорий берутся из `find_junk_files_deep` — с раскрытием шаблонов,
    защитой путей и правилом «каталог принадлежит первой нашедшей категории».
    Возвращает словари с источником, категорией, путём и ограничениями.
    """
    running = cleaner._running_process_names()
    targets: list[dict[str, Any]] = []
    for category, details in cleaner.STANDARD_PLAN.items():
        if details["type"] == "command":
            continue
        for raw in details["paths"]:
            targets.append(
                {
                    "source": "standard",
                    "category": category,
                    "path": os.path.expandvars(raw),
                    "min_age": cleaner._min_age_seconds(details),
                    "approved": True,
                    "blockers": [],
                    "rule": details,
                }
            )

    levels = None if include_medium else {SAFE_LEVEL}
    report = asyncio.run(cleaner.find_junk_files_deep(levels))
    by_id = {rule["category_id"]: rule for rule in rules}
    for category, found in report.items():
        rule = by_id[category]
        safety = str(rule.get("safety", "medium")).lower()
        blockers = sorted(
            f"запущено {x}" for x in rule.get("requires_closed") or [] if x.lower() in running
        )
        files = found.get("files_to_delete") or []
        if files:
            targets.append(
                {
                    "source": "kb",
                    "category": category,
                    "path": (
                        f"{Path(files[0]).parent} (+ ещё {len(files) - 1})"
                        if len(files) > 1
                        else files[0]
                    ),
                    "files": files,
                    "min_age": None,
                    "approved": safety == SAFE_LEVEL,
                    "blockers": blockers,
                    "rule": rule,
                }
            )
        for folder in found.get("folders_to_clean") or []:
            targets.append(
                {
                    "source": "kb",
                    "category": category,
                    "path": folder,
                    "min_age": cleaner._min_age_seconds(rule),
                    "approved": safety == SAFE_LEVEL,
                    "blockers": blockers,
                    "rule": rule,
                }
            )
    return targets


def audit_path(cleaner: SmartCleaner, target: dict[str, Any]) -> dict[str, Any]:
    """Сухой прогон одного пути с теми же ограничениями, что при удалении."""
    path, rule = target["path"], target["rule"]
    entry: dict[str, Any] = {"path": path, "exists": False, "guard": "ok", "busy": ""}
    if target.get("files"):
        # Файлы, найденные файловым правилом базы знаний (например, журналы Unity).
        files = [Path(f) for f in target["files"]]
        entry["exists"] = True
        entry["mask_files"] = len(files)
        entry["mask_bytes"] = sum(f.stat().st_size for f in files if f.is_file())
        probes = [engine._probe_deletable(str(f), is_dir=False) for f in files[:500]]
        entry["mask_status"] = Counter(status for status, _ in probes)
        return entry
    if "*" in path:
        # Маски стандартного плана: тот же поиск, что у программы, без удаления.
        found = cleaner._find_files_by_mask(path, rule)
        entry["exists"] = True
        entry["mask_files"] = len(found)
        entry["mask_bytes"] = sum(size for _, size in found)
        probes = [engine._probe_deletable(str(p), is_dir=False) for p, _ in found[:500]]
        entry["mask_status"] = Counter(status for status, _ in probes)
        return entry

    folder = Path(path)
    if not folder.is_dir():
        return entry
    entry["exists"] = True
    if not cleaner.is_safe_to_delete(folder):
        entry["guard"] = "refused"  # защита путей программы отклоняет цель целиком
        return entry
    if rule.get("skip_if_busy"):
        entry["busy"] = cleaner._busy_file(folder, rule)

    started = time.perf_counter()
    entry["result"] = engine.process_tree(
        folder,
        mode="audit",
        min_age_seconds=target["min_age"],
        atomic_subdirs=bool(rule.get("atomic_subdirs")),
    )
    entry["seconds"] = time.perf_counter() - started
    return entry


def explain_denied(samples: list[engine.FileRecord]) -> list[dict[str, Any]]:
    """Владелец и права администраторов для недоступных объектов (по каталогам)."""
    by_dir: dict[str, list[engine.FileRecord]] = defaultdict(list)
    for record in samples:
        by_dir[str(Path(record.path).parent)].append(record)

    rows = []
    for directory, records in by_dir.items():
        probe = records[0]
        access = engine.describe_access(probe.path)
        parent = engine.describe_access(directory)
        rows.append(
            {
                "dir": directory,
                "examples": len(records),
                "owner": access["owner"],
                "admins_can_delete": access["admins_can_delete"],
                "parent_owner": parent["owner"],
                "parent_admins": parent["admins_can_delete"],
                "note": access["note"] or parent["note"],
                "sample": Path(probe.path).name,
            }
        )
    return rows


def explain_locked(samples: list[engine.FileRecord]) -> dict[str, list[str]]:
    """Какие процессы держат занятые файлы (по каталогам, с ограничением)."""
    by_dir: dict[str, list[str]] = defaultdict(list)
    for record in samples[:LOCK_HOLDER_PROBES]:
        by_dir[str(Path(record.path).parent)].append(record.path)
    return {directory: engine.find_lock_holders(paths) for directory, paths in by_dir.items()}


def verdict(row: dict[str, Any]) -> str:
    """Стоит ли давать программе больше прав ради этого каталога."""
    owner = (row["owner"] or "").lower()
    if row["admins_can_delete"] or row["parent_admins"]:
        return "хватит прав администратора (программа их уже запрашивает)"
    if "trustedinstaller" in owner or "system" in owner:
        return "закрыто даже для администраторов — не трогать"
    if row["admins_can_delete"] is None:
        return "ACL не прочитан — решать вручную"
    return "нужен захват владения — небезопасно, не трогать"


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--out", type=Path, help="куда сохранить Markdown-отчёт")
    parser.add_argument(
        "--no-medium", action="store_true", help="не показывать категории safety: medium"
    )
    args = parser.parse_args()

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(encoding="utf-8", errors="replace")

    rules = load_rules()
    cleaner = SmartCleaner(rules)
    admin = is_admin()
    targets = collect_targets(cleaner, rules, include_medium=not args.no_medium)

    lines: list[str] = []
    out = lines.append
    out(f"# Сухой прогон очистки — {datetime.now():%Y-%m-%d %H:%M}")
    out("")
    out(
        f"Пользователь: `{os.environ.get('USERNAME')}`, права администратора: **{'да' if admin else 'нет'}**."
    )
    out("Ничего не удалялось. Столбец «нет прав» показывает отказы для *текущего* пользователя;")
    out("приложение работает от администратора, поэтому отдельно оценено, хватит ли ему этих прав.")
    out("")

    totals: dict[str, engine.TreeResult] = {}
    denied_all: list[engine.FileRecord] = []
    locked_all: list[engine.FileRecord] = []
    rows = []
    started = time.perf_counter()

    for target in targets:
        entry = audit_path(cleaner, target)
        blockers = target["blockers"] or (
            [f"кеш занят ({Path(entry['busy']).name})"] if entry["busy"] else []
        )
        entry["blockers"] = blockers
        approved = target["approved"]
        rows.append((target["source"], target["category"], "", approved, entry))
        result = entry.get("result")
        if result is not None:
            key = "approved" if approved and not blockers else "medium"
            totals.setdefault(key, engine.TreeResult(root="*", mode="audit")).merge(result)
            if approved and not blockers:
                denied_all.extend(result.samples[engine.STATUS_ACCESS_DENIED])
                locked_all.extend(result.samples[engine.STATUS_LOCKED])
    elapsed = time.perf_counter() - started

    def table(title: str, subset: list) -> None:
        out(f"## {title}")
        out("")
        out("| Категория | Путь | Удалит | Свежие | Заняты | Нет прав | В работе | Каталогов |")
        out("|---|---|---:|---:|---:|---:|---:|---:|")
        for source, category, _safety, _approved, entry in subset:
            label = f"`{category}`" + ("" if source == "kb" else " *(стандарт)*")
            if entry.get("blockers"):
                label += f" — *отложено: {', '.join(entry['blockers'])}*"
            if not entry["exists"]:
                out(f"| {label} | `{entry['path']}` | — | | | | нет пути |")
                continue
            if entry["guard"] == "refused":
                out(f"| {label} | `{entry['path']}` | **отклонено защитой путей** | | | | |")
                continue
            if "mask_files" in entry:
                st = entry["mask_status"]
                out(
                    f"| {label} | `{entry['path']}` | {st.get('deletable', 0)} ф. / {fmt_size(entry['mask_bytes'])} "
                    f"| | {st.get('locked', 0)} | {st.get('access_denied', 0)} | маска |"
                )
                continue
            r = entry["result"]
            out(
                f"| {label} | `{entry['path']}` | {r.freed_files} ф. / {fmt_size(r.freed_bytes)} "
                f"| {r.counts['too_recent']} ({fmt_size(r.sizes['too_recent'])}) "
                f"| {r.counts['locked']} ({fmt_size(r.sizes['locked'])}) "
                f"| {r.counts['access_denied']} ({fmt_size(r.sizes['access_denied'])}) "
                f"| {r.counts['in_use']} ({fmt_size(r.sizes['in_use'])}) "
                f"| {r.dirs_seen} |"
            )
        out("")

    approved_rows = [r for r in rows if r[3]]
    medium_rows = [r for r in rows if not r[3]]
    table("Что офлайн-режим удалил бы сейчас (стандартный план + safety: high)", approved_rows)
    if medium_rows:
        table("Для справки: категории safety: medium (без ИИ не трогаются)", medium_rows)

    if "approved" in totals:
        t = totals["approved"]
        out("## Итог по авто-одобренным целям")
        out("")
        out(
            f"- Удалит: **{t.freed_files} файлов, {fmt_size(t.freed_bytes)}** (каталоги с `requires_closed` при запущенной программе не считаются)"
        )
        out(
            f"- Пропустит как свежие (порог правила): {t.counts['too_recent']} файлов, {fmt_size(t.sizes['too_recent'])}"
        )
        out(
            f"- Пропустит подкаталоги temp «в работе»: {t.counts['in_use']} файлов, {fmt_size(t.sizes['in_use'])}"
        )
        out(
            f"- Пропустит как занятые процессами: {t.counts['locked']} файлов, {fmt_size(t.sizes['locked'])}"
        )
        out(
            f"- Нет прав у текущего пользователя: {t.counts['access_denied']} объектов, {fmt_size(t.sizes['access_denied'])}"
        )
        out(f"- Прочие ошибки: {t.counts['error']}")
        out(f"- Время прогона: {elapsed:.1f} с")
        out("")

    out("## Где не хватает прав — и стоит ли их давать")
    out("")
    if not denied_all:
        out("Отказов по правам не было.")
    else:
        out("| Каталог | Владелец | Админы могут удалять | Пример | Вердикт |")
        out("|---|---|---|---|---|")
        for row in explain_denied(denied_all):
            admins = row["admins_can_delete"]
            admins_txt = "да" if admins else ("нет" if admins is False else "?")
            if row["parent_admins"] and not admins:
                admins_txt += " (через каталог)"
            out(
                f"| `{row['dir']}` | {row['owner']} | {admins_txt} | `{row['sample']}` | {verdict(row)} |"
            )
    out("")

    out("## Кто держит занятые файлы")
    out("")
    if not locked_all:
        out("Занятых файлов не было.")
    else:
        holders = explain_locked(locked_all)
        for directory, procs in holders.items():
            out(f"- `{directory}`: {', '.join(procs) if procs else 'держатель не определён'}")
    out("")

    out("## Остатки удалённых программ")
    out("")
    out("Каталог попадает сюда только при улике удаления (битая запись Uninstall, битый ярлык,")
    out("мёртвый путь в реестре или в следах запуска). В боевом режиме каталоги с оценкой")
    out("**high** переезжают в карантин на 30 дней, **low** — только показываются.")
    out("")
    leftovers = leftover_scanner.scan_leftovers(is_safe=cleaner.is_safe_to_delete)
    out(
        f"Улик собрано: {len(leftovers.evidence)}, призрачных записей Uninstall: "
        f"{len(leftovers.ghost_uninstall_entries)}, время: {leftovers.seconds:.1f} с."
    )
    out("")
    for note in leftovers.notes:
        out(f"> {note}")
        out("")
    if not leftovers.candidates:
        out("Кандидатов нет.")
    else:
        out("| Оценка | Каталог | Размер | Файлов | Улика | Почему не трогаем |")
        out("|---|---|---:|---:|---|---|")
        for c in leftovers.candidates:
            reason = c.evidence[0] if c.evidence else ""
            out(
                f"| {c.confidence} | `{c.path}` | {fmt_size(c.size_bytes)} | {c.file_count} "
                f"| {reason[:90]} | {c.kept_reason} |"
            )
    if leftovers.ghost_uninstall_entries:
        out("")
        out(
            "Призрачные записи в Programs and Features (только показываются): "
            + ", ".join(f"`{name}`" for name in leftovers.ghost_uninstall_entries)
        )
    out("")

    out("## Пустые папки программ")
    out("")
    out("В дереве нет ни одного файла. В боевом режиме папки без причины удаляются")
    out("последним шагом, после файлов, — только `rmdir`, каталог с файлами не удалится.")
    out("")
    if not leftovers.empty_dirs:
        out("Пустых папок программ нет.")
    else:
        out("| Папка | Каталогов | Изменена | Почему не трогаем |")
        out("|---|---:|---|---|")
        for d in sorted(leftovers.empty_dirs, key=lambda d: (bool(d.kept_reason), d.path)):
            changed = datetime.fromtimestamp(d.newest_mtime).strftime("%Y-%m-%d")
            out(f"| `{d.path}` | {d.dir_count} | {changed} | {d.kept_reason} |")
    out("")

    report = "\n".join(lines)
    print(report)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(report, encoding="utf-8")
        print(f"\nОтчёт сохранён: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
