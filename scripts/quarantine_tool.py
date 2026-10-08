# scripts/quarantine_tool.py
"""
Управление карантином остатков удалённых программ.

    python scripts/quarantine_tool.py list             — партии и их содержимое
    python scripts/quarantine_tool.py restore <партия> — вернуть каталоги на место
    python scripts/quarantine_tool.py purge            — удалить партии старше срока

Партия — имя подкаталога вида `20260918-030500` в
`%LOCALAPPDATA%\\WinSpectorPro\\Quarantine`. Восстановление ничего не
перезаписывает: если на исходном месте уже что-то появилось, каталог
остаётся в карантине, а причина печатается.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.winspector.core.modules import quarantine  # noqa: E402


def fmt_size(value: int) -> str:
    for unit, threshold in (("ГБ", 1024**3), ("МБ", 1024**2), ("КБ", 1024)):
        if value >= threshold:
            return f"{value / threshold:.1f} {unit}"
    return f"{value} Б"


def cmd_list() -> int:
    batches = quarantine.list_batches()
    if not batches:
        print(f"Карантин пуст: {quarantine.quarantine_root()}")
        return 0
    for batch in batches:
        payload = json.loads((batch / quarantine.MANIFEST_NAME).read_text(encoding="utf-8"))
        items = payload.get("items", [])
        total = sum(int(item.get("size_bytes", 0)) for item in items)
        print(
            f"{batch.name}  ({len(items)} каталогов, {fmt_size(total)}, создано {payload.get('created_at')})"
        )
        for item in items:
            print(f"    {item['original']}")
            print(f"        причина: {item.get('reason', '')}")
    return 0


def cmd_restore(name: str) -> int:
    batch = quarantine.quarantine_root() / name
    if not (batch / quarantine.MANIFEST_NAME).is_file():
        print(f"Партия не найдена: {batch}", file=sys.stderr)
        return 1
    restored, errors = quarantine.restore_batch(batch)
    print(f"Восстановлено каталогов: {restored}")
    for path, reason in errors.items():
        print(f"    не восстановлено {path}: {reason}")
    return 0 if not errors else 2


def cmd_purge() -> int:
    purged = quarantine.purge_expired()
    print(f"Удалено партий старше {quarantine.RETENTION_DAYS} дней: {purged}")
    return 0


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list")
    restore = sub.add_parser("restore")
    restore.add_argument("batch")
    sub.add_parser("purge")
    args = parser.parse_args()

    if args.command == "list":
        return cmd_list()
    if args.command == "restore":
        return cmd_restore(args.batch)
    return cmd_purge()


if __name__ == "__main__":
    sys.exit(main())
