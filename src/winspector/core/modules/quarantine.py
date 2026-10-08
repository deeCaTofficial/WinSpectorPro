# src/winspector/core/modules/quarantine.py
"""
Карантин для остатков удалённых программ.

Остаток нельзя отличить от нужных данных со стопроцентной уверенностью:
в каталоге настроек удалённой игры могут лежать сохранения, которые
пригодятся после переустановки. Поэтому такие каталоги не удаляются, а
**переносятся** в `%LOCALAPPDATA%\\WinSpectorPro\\Quarantine\\<дата>\\`.
Перенос внутри тома — это переименование: мгновенно и без копирования.
Всё, что старше `RETENTION_DAYS`, удаляется окончательно при следующем
запуске; до этого каталог можно вернуть на место.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import shutil
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from . import cleanup_engine as engine

logger = logging.getLogger(__name__)

RETENTION_DAYS = 30
MANIFEST_NAME = "manifest.json"
_BATCH_FORMAT = "%Y%m%d-%H%M%S"


def quarantine_root() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    root = Path(base) if base else Path.home()
    return root / "WinSpectorPro" / "Quarantine"


@dataclass
class QuarantinedItem:
    original: str
    stored: str
    size_bytes: int
    reason: str


@dataclass
class QuarantineResult:
    batch_dir: str = ""
    items: list[QuarantinedItem] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)  # путь -> причина

    @property
    def moved_bytes(self) -> int:
        return sum(item.size_bytes for item in self.items)


def _safe_name(path: Path) -> str:
    """Имя для хранения: последние две части пути, без запрещённых символов."""
    parts = [p for p in path.parts[-2:] if p and not re.fullmatch(r"[A-Za-z]:\\?", p)]
    name = "__".join(parts) or path.name or "item"
    return re.sub(r'[<>:"/\\|?*]', "_", name)[:120]


def quarantine_paths(
    targets: list[tuple[Path, int, str] | tuple[Path, int, str, bool]],
    *,
    root: Path | None = None,
    now: datetime | None = None,
) -> QuarantineResult:
    """
    Переносит каталоги в карантин.

    Args:
        targets: (путь, размер, причина[, убирать_пустой_родитель]). Путь —
            каталог или файл. Размер и причина попадают в манифест, чтобы при
            восстановлении было понятно, что это.
    """
    result = QuarantineResult()
    if not targets:
        return result

    root = root if root is not None else quarantine_root()
    stamp = (now or datetime.now()).strftime(_BATCH_FORMAT)
    batch = root / stamp
    try:
        batch.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        for target in targets:
            result.skipped[str(target[0])] = f"карантин недоступен: {exc}"
        return result
    result.batch_dir = str(batch)

    for index, target in enumerate(targets, start=1):
        path, size, reason = target[:3]
        prune_parent = target[3] if len(target) > 3 else True
        source = Path(path)
        if not source.exists():
            result.skipped[str(source)] = "уже отсутствует"
            continue
        destination = batch / f"{index:03d}__{_safe_name(source)}"
        try:
            # `os.rename`, а не `shutil.move`: на другом томе move скопировал
            # бы дерево и удалил оригинал — это уже не «перенос в карантин».
            os.rename(source, destination)  # noqa: PTH104 — намеренно без копирования
        except OSError as exc:
            code = getattr(exc, "winerror", None)
            if code == 17:  # ERROR_NOT_SAME_DEVICE
                result.skipped[str(source)] = "другой том: перенос без копирования невозможен"
            elif code == 32:
                result.skipped[str(source)] = "каталог занят другим процессом"
            else:
                result.skipped[str(source)] = engine._describe(exc)
            continue
        result.items.append(QuarantinedItem(str(source), str(destination), size, reason))
        if prune_parent:
            _prune_empty_parent(source.parent)

    _write_manifest(batch, result)
    if not result.items:
        # Пустая партия не нужна.
        shutil.rmtree(batch, ignore_errors=True)
        result.batch_dir = ""
    return result


def _prune_empty_parent(parent: Path) -> None:
    """Каталог издателя, опустевший после переноса, тоже убирается (rmdir)."""
    with contextlib.suppress(OSError):  # не пуст или это корень — оставляем
        parent.rmdir()


def _write_manifest(batch: Path, result: QuarantineResult) -> None:
    payload = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "retention_days": RETENTION_DAYS,
        "items": [asdict(item) for item in result.items],
    }
    try:
        (batch / MANIFEST_NAME).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError as exc:
        logger.warning("Не удалось записать манифест карантина: %s", exc)


# --- Восстановление и очистка -------------------------------------------------


def list_batches(root: Path | None = None) -> list[Path]:
    root = root if root is not None else quarantine_root()
    if not root.is_dir():
        return []
    return sorted(p for p in root.iterdir() if p.is_dir() and (p / MANIFEST_NAME).is_file())


def batch_created(batch: Path) -> datetime | None:
    """Когда партия попала в карантин: папка партии названа этим временем."""
    try:
        return datetime.strptime(batch.name, _BATCH_FORMAT)
    except ValueError:
        return None


def restore_batch(batch: Path) -> tuple[int, dict[str, str]]:
    """Возвращает каталоги партии на исходные места. Итог: (восстановлено, ошибки)."""
    try:
        payload = json.loads((batch / MANIFEST_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return 0, {str(batch): f"манифест не прочитан: {exc}"}

    restored = 0
    errors: dict[str, str] = {}
    for item in payload.get("items", []):
        stored, original = Path(item["stored"]), Path(item["original"])
        if not stored.exists():
            errors[str(original)] = "в карантине уже нет"
            continue
        if original.exists():
            errors[str(original)] = "на исходном месте уже что-то есть"
            continue
        try:
            original.parent.mkdir(parents=True, exist_ok=True)
            os.rename(stored, original)  # noqa: PTH104
            restored += 1
        except OSError as exc:
            errors[str(original)] = engine._describe(exc)
    if restored and not errors:
        shutil.rmtree(batch, ignore_errors=True)
    return restored, errors


def purge_expired(
    *, root: Path | None = None, retention_days: int = RETENTION_DAYS, now: float | None = None
) -> int:
    """Окончательно удаляет партии старше срока хранения. Возвращает их число."""
    cutoff = (now if now is not None else time.time()) - retention_days * 86400
    purged = 0
    for batch in list_batches(root):
        created = batch_created(batch)
        if created is None or created.timestamp() >= cutoff:
            continue
        # Содержимое карантина — уже отобранный мусор, здесь удаление
        # окончательное; ошибки не должны мешать остальным партиям.
        shutil.rmtree(batch, ignore_errors=True)
        if not batch.exists():
            purged += 1
            logger.info("Карантин: партия %s удалена по истечении срока.", batch.name)
    return purged
