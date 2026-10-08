# src/winspector/core/modules/cleanup_engine.py
"""
Движок очистки каталогов: обход, оценка и удаление файлов, затем — пустых
каталогов.

Один и тот же обход работает в трёх режимах:

* ``scan``  — только размер того, что подлежит удалению (быстро, без проб);
* ``audit`` — «сухой прогон»: для каждого файла без удаления проверяется,
              *можно ли* его удалить (занят? нет прав?), и почему нет;
* ``apply`` — реальное удаление.

Правила, одинаковые для всех режимов:

1.  **Сначала файлы, потом каталоги.** Каталог удаляется только после того,
    как обход попытался удалить его содержимое, и только через `os.rmdir`:
    система сама откажет, если внутри что-то осталось или появилось между
    проверкой и удалением. Никакого `shutil.rmtree`.
2.  **Ссылки не раскрываются.** Символические ссылки и junction-точки
    удаляются как ссылки, внутрь никогда не заходим: `os.walk` и
    `DirEntry.is_dir(follow_symlinks=False)` junction-точки *раскрывают*,
    поэтому проверка `is_junction()` здесь явная.
3.  **Свежие файлы не трогаются.** Файл, изменённый позже порога `min_age`,
    считается рабочим файлом запущенной программы — как в Disk Cleanup и
    CCleaner для временных каталогов.
3a. **Каталог в работе не разбирается по частям** (`atomic_subdirs`). Во
    временных каталогах подкаталог первого уровня — это рабочая папка одной
    программы (распакованный установщик, профиль браузера, `_MEI` PyInstaller).
    Если в нём есть хоть один свежий или занятый файл, программа им пользуется,
    и удаление «старых» файлов из середины сломало бы её. Такой подкаталог
    пропускается целиком.
4.  **Честный учёт.** Считаются только реально удалённые файлы и байты;
    занятые, свежие и недоступные файлы учитываются отдельно с причиной.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import logging
import os
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger(__name__)

try:
    import pywintypes
    import win32con
    import win32file
    import winerror
except ImportError:  # окружения без pywin32
    pywintypes = win32con = win32file = winerror = None  # type: ignore[assignment]

Mode = Literal["scan", "audit", "apply"]

# Статусы файла. `deletable` — удаление возможно (audit) или не проверялось
# (scan); `deleted` — удалён; остальные — причина, по которой не удалён.
STATUS_DELETABLE = "deletable"
STATUS_DELETED = "deleted"
STATUS_TOO_RECENT = "too_recent"
STATUS_LOCKED = "locked"
STATUS_ACCESS_DENIED = "access_denied"
STATUS_IN_USE = "in_use"  # весь подкаталог пропущен: им пользуется программа
STATUS_ERROR = "error"
ALL_STATUSES = (
    STATUS_DELETABLE,
    STATUS_DELETED,
    STATUS_TOO_RECENT,
    STATUS_LOCKED,
    STATUS_ACCESS_DENIED,
    STATUS_IN_USE,
    STATUS_ERROR,
)
_SKIPPED_STATUSES = (STATUS_TOO_RECENT, STATUS_LOCKED, STATUS_ACCESS_DENIED, STATUS_IN_USE)

_WINERROR_STATUS = {
    32: STATUS_LOCKED,  # ERROR_SHARING_VIOLATION
    33: STATUS_LOCKED,  # ERROR_LOCK_VIOLATION
    5: STATUS_ACCESS_DENIED,  # ERROR_ACCESS_DENIED
    1920: STATUS_ACCESS_DENIED,  # ERROR_CANT_ACCESS_FILE
}

# Сколько примеров каждого статуса хранить для отчёта.
DEFAULT_SAMPLE_LIMIT = 40

# Открывать саму ссылку, а не её цель (в win32con константы нет).
_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000

# Флаги дескриптора безопасности: наследование отключено; запись унаследована.
_SE_DACL_PROTECTED = 0x1000
_INHERITED_ACE = 0x10


@dataclass(slots=True)
class FileRecord:
    """Один файл (или ссылка) из обхода с вердиктом."""

    path: str
    size: int
    status: str
    detail: str = ""
    is_link: bool = False
    mtime: float = 0.0


@dataclass
class TreeResult:
    """Итог обхода одного каталога."""

    root: str
    mode: Mode
    counts: dict[str, int] = field(default_factory=lambda: dict.fromkeys(ALL_STATUSES, 0))
    sizes: dict[str, int] = field(default_factory=lambda: dict.fromkeys(ALL_STATUSES, 0))
    dirs_seen: int = 0
    dirs_removed: int = 0
    dirs_errors: int = 0
    samples: dict[str, list[FileRecord]] = field(
        default_factory=lambda: {status: [] for status in ALL_STATUSES}
    )
    walk_error: str = ""

    # --- Сводные значения --------------------------------------------------

    @property
    def freed_bytes(self) -> int:
        """Байты, реально освобождённые (apply) либо подлежащие удалению."""
        return self.sizes[STATUS_DELETED] + self.sizes[STATUS_DELETABLE]

    @property
    def freed_files(self) -> int:
        return self.counts[STATUS_DELETED] + self.counts[STATUS_DELETABLE]

    @property
    def skipped_files(self) -> int:
        return sum(self.counts[status] for status in _SKIPPED_STATUSES)

    @property
    def skipped_bytes(self) -> int:
        return sum(self.sizes[status] for status in _SKIPPED_STATUSES)

    @property
    def errors(self) -> int:
        return self.counts[STATUS_ERROR] + self.dirs_errors + (1 if self.walk_error else 0)

    def record(self, entry: FileRecord, sample_limit: int, *, count: int = 1) -> None:
        self.counts[entry.status] += count
        self.sizes[entry.status] += entry.size
        bucket = self.samples[entry.status]
        if len(bucket) < sample_limit:
            bucket.append(entry)

    def merge(self, other: TreeResult) -> None:
        for status in ALL_STATUSES:
            self.counts[status] += other.counts[status]
            self.sizes[status] += other.sizes[status]
            room = DEFAULT_SAMPLE_LIMIT - len(self.samples[status])
            if room > 0:
                self.samples[status].extend(other.samples[status][:room])
        self.dirs_seen += other.dirs_seen
        self.dirs_removed += other.dirs_removed
        self.dirs_errors += other.dirs_errors
        if other.walk_error and not self.walk_error:
            self.walk_error = other.walk_error


# --- Обход -------------------------------------------------------------------


def process_tree(
    root: Path | str,
    *,
    mode: Mode,
    min_age_seconds: float | None = None,
    sample_limit: int = DEFAULT_SAMPLE_LIMIT,
    now: float | None = None,
    atomic_subdirs: bool = False,
) -> TreeResult:
    """
    Обрабатывает содержимое каталога `root`; сам `root` не удаляется.

    В режиме ``apply`` файлы удаляются по мере обхода, затем каталоги —
    от самых глубоких к корню, через `os.rmdir`. С `atomic_subdirs`
    подкаталоги первого уровня обрабатываются как единое целое (см. 3a).
    """
    root_str = str(root)
    result = TreeResult(root=root_str, mode=mode)
    now = now if now is not None else time.time()
    cutoff = None
    if min_age_seconds is not None:
        cutoff = now - min_age_seconds

    if not _is_real_directory(root_str, result):
        return result
    if atomic_subdirs:
        return _process_atomic(root_str, result, mode, min_age_seconds, cutoff, sample_limit, now)

    # Каталоги в порядке обхода; для второй фазы нужен обратный порядок.
    visited_dirs: list[str] = []
    stack = [root_str]

    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                children = list(entries)
        except OSError as exc:
            _note_dir_error(result, current, exc)
            continue

        visited_dirs.append(current)
        for entry in children:
            _handle_entry(entry, result, mode, cutoff, sample_limit, stack)

    if mode == "apply":
        _remove_empty_dirs(visited_dirs, root_str, result)
    else:
        # Для отчёта: сколько каталогов затронуто (без корня).
        result.dirs_seen = max(0, len(visited_dirs) - 1)

    return result


def _is_real_directory(root: str, result: TreeResult) -> bool:
    """Корень существует и не является ссылкой: за junction может быть что угодно."""
    if not os.path.isdir(root):
        return False
    try:
        if Path(root).is_symlink() or Path(root).is_junction():
            result.walk_error = "корень — ссылка или junction-точка, обход не выполнялся"
            return False
    except OSError:
        return False
    return True


def _process_atomic(
    root: str,
    result: TreeResult,
    mode: Mode,
    min_age_seconds: float | None,
    cutoff: float | None,
    sample_limit: int,
    now: float,
) -> TreeResult:
    """Файлы корня — по одному; подкаталоги первого уровня — целиком или никак."""
    try:
        with os.scandir(root) as entries:
            children = list(entries)
    except OSError as exc:
        result.walk_error = _describe(exc)
        return result

    subdirs: list[str] = []
    for entry in children:
        try:
            plain_dir = (
                entry.is_dir(follow_symlinks=False)
                and not entry.is_symlink()
                and not entry.is_junction()
            )
        except OSError:
            plain_dir = False
        if plain_dir:
            subdirs.append(entry.path)
        else:
            _handle_entry(entry, result, mode, cutoff, sample_limit, [])

    for subdir in subdirs:
        # Проба без изменений: в `scan` — только возраст, иначе ещё и блокировки.
        probe = process_tree(
            subdir,
            mode="scan" if mode == "scan" else "audit",
            min_age_seconds=min_age_seconds,
            sample_limit=sample_limit,
            now=now,
        )
        result.dirs_seen += 1 + probe.dirs_seen
        if probe.counts[STATUS_TOO_RECENT] or probe.counts[STATUS_LOCKED]:
            files = sum(probe.counts.values())
            size = sum(probe.sizes.values())
            result.record(
                FileRecord(
                    path=subdir,
                    size=size,
                    status=STATUS_IN_USE,
                    detail="в каталоге есть свежие или занятые файлы — программа им пользуется",
                ),
                sample_limit,
                count=files,
            )
            continue

        if mode != "apply":
            probe.dirs_seen = 0  # уже учтено выше
            result.merge(probe)
            continue

        applied = process_tree(
            subdir,
            mode="apply",
            min_age_seconds=min_age_seconds,
            sample_limit=sample_limit,
            now=now,
        )
        applied.dirs_seen = 0
        result.merge(applied)
        try:
            os.rmdir(subdir)
            result.dirs_removed += 1
        except OSError as exc:
            if getattr(exc, "winerror", None) not in (145, 41):
                result.dirs_errors += 1
    return result


def first_locked_file(root: Path | str, *, limit: int = 100_000) -> str:
    """
    Первый занятый другим процессом файл в дереве или пустая строка.

    Только чтение: каждый файл открывается с правом `DELETE` и сразу
    закрывается (см. `_probe_deletable`). Обход останавливается на первом
    занятом файле — для ответа «программа запущена?» этого достаточно.
    """
    if not os.path.isdir(str(root)):
        return ""
    stack = [str(root)]
    seen = 0
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                children = list(entries)
        except OSError:
            continue
        for entry in children:
            try:
                if entry.is_symlink() or entry.is_junction():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    stack.append(entry.path)
                    continue
            except OSError:
                continue
            seen += 1
            if seen > limit:
                return ""
            status, _ = _probe_deletable(entry.path, is_dir=False)
            if status == STATUS_LOCKED:
                return entry.path
    return ""


def _handle_entry(
    entry: os.DirEntry[str],
    result: TreeResult,
    mode: Mode,
    cutoff: float | None,
    sample_limit: int,
    stack: list[str],
) -> None:
    path = entry.path
    try:
        is_link = entry.is_symlink() or entry.is_junction()
    except OSError:
        is_link = False

    if is_link:
        # Ссылка удаляется сама по себе; её цель нас не касается.
        record = FileRecord(path=path, size=0, status=STATUS_DELETABLE, is_link=True)
        if mode == "apply":
            record.status, record.detail = _unlink_link(path)
        elif mode == "audit":
            # Для ссылки на каталог без FILE_FLAG_BACKUP_SEMANTICS система
            # отвечает ACCESS_DENIED; для файла флаг безвреден.
            record.status, record.detail = _probe_deletable(path, is_dir=True)
        result.record(record, sample_limit)
        return

    try:
        if entry.is_dir(follow_symlinks=False):
            stack.append(path)
            return
        if not entry.is_file(follow_symlinks=False):
            return
        stat = entry.stat(follow_symlinks=False)
    except OSError as exc:
        result.record(
            FileRecord(path=path, size=0, status=STATUS_ERROR, detail=_describe(exc)),
            sample_limit,
        )
        return

    record = FileRecord(path=path, size=stat.st_size, status=STATUS_DELETABLE, mtime=stat.st_mtime)

    if cutoff is not None and stat.st_mtime > cutoff:
        record.status = STATUS_TOO_RECENT
    elif mode == "apply":
        record.status, record.detail = _unlink_file(path)
    elif mode == "audit":
        record.status, record.detail = _probe_deletable(path, is_dir=False)

    result.record(record, sample_limit)


def _remove_empty_dirs(visited_dirs: list[str], root: str, result: TreeResult) -> None:
    """Вторая фаза: `rmdir` от глубоких каталогов к корню; корень остаётся."""
    for directory in reversed(visited_dirs):
        if directory == root:
            continue
        result.dirs_seen += 1
        try:
            os.rmdir(directory)
            result.dirs_removed += 1
        except OSError as exc:
            # ERROR_DIR_NOT_EMPTY (145): внутри остались занятые или свежие
            # файлы — каталог сохраняется, это штатный исход.
            if getattr(exc, "winerror", None) not in (145, 41):
                result.dirs_errors += 1
                logger.debug("Каталог не удалён '%s': %s", directory, exc)


# --- Операции над одним элементом ------------------------------------------


def _unlink_file(path: str) -> tuple[str, str]:
    try:
        os.unlink(path)
        return STATUS_DELETED, ""
    except FileNotFoundError:
        # Исчез между обходом и удалением — освободили не мы.
        return STATUS_ERROR, "файл исчез до удаления"
    except OSError as exc:
        return _status_from_oserror(exc), _describe(exc)


def _unlink_link(path: str) -> tuple[str, str]:
    """Снимает ссылку, не трогая цель: файл-ссылка — unlink, каталог — rmdir."""
    try:
        try:
            os.unlink(path)
        except (IsADirectoryError, PermissionError, OSError):
            os.rmdir(path)
        return STATUS_DELETED, ""
    except OSError as exc:
        return _status_from_oserror(exc), _describe(exc)


def _probe_deletable(path: str, *, is_dir: bool) -> tuple[str, str]:
    """
    Проверяет право и возможность удаления, ничего не удаляя.

    Открытие с правом `DELETE` и полным разделением доступа отвечает на
    оба вопроса сразу: ACL запрещает — ERROR_ACCESS_DENIED, файл держит
    другой процесс без FILE_SHARE_DELETE — ERROR_SHARING_VIOLATION.
    Само по себе открытие ничего не меняет и не удаляет.
    """
    if win32file is None:
        return STATUS_DELETABLE, "pywin32 недоступен — проверка не выполнялась"
    flags = win32con.FILE_FLAG_BACKUP_SEMANTICS if is_dir else 0
    flags |= _FILE_FLAG_OPEN_REPARSE_POINT
    try:
        handle = win32file.CreateFileW(
            path,
            win32con.DELETE,
            win32con.FILE_SHARE_READ | win32con.FILE_SHARE_WRITE | win32con.FILE_SHARE_DELETE,
            None,
            win32con.OPEN_EXISTING,
            flags,
            None,
        )
    except pywintypes.error as exc:
        status = _WINERROR_STATUS.get(exc.winerror, STATUS_ERROR)
        return status, f"{exc.strerror} (код {exc.winerror})"
    handle.Close()
    return STATUS_DELETABLE, ""


def _status_from_oserror(exc: OSError) -> str:
    code = getattr(exc, "winerror", None)
    if code is None and isinstance(exc, PermissionError):
        return STATUS_ACCESS_DENIED
    return _WINERROR_STATUS.get(code, STATUS_ERROR)


def _describe(exc: OSError) -> str:
    code = getattr(exc, "winerror", None)
    text = exc.strerror or exc.__class__.__name__
    return f"{text} (код {code})" if code is not None else text


def _note_dir_error(result: TreeResult, directory: str, exc: OSError) -> None:
    if directory == result.root:
        result.walk_error = _describe(exc)
        return
    # Каталог, куда нет входа: считаем одной недоступной записью, чтобы
    # он попал в отчёт о правах.
    status = _status_from_oserror(exc)
    result.record(
        FileRecord(path=directory, size=0, status=status, detail=_describe(exc)),
        DEFAULT_SAMPLE_LIMIT,
    )


# --- Диагностика для аудита ---------------------------------------------------


def describe_access(path: str) -> dict[str, Any]:
    """
    Кто владеет объектом и могут ли администраторы его удалить.

    Нужно, чтобы отличить «не хватило прав обычному пользователю» (приложение
    и так работает от администратора — ему хватит) от «объект принадлежит
    TrustedInstaller/SYSTEM и закрыт даже для администраторов» — такие файлы
    трогать не следует вовсе.
    """
    try:
        import win32security
    except ImportError:
        return {"owner": "?", "admins_can_delete": None, "note": "pywin32 недоступен"}

    info: dict[str, Any] = {
        "owner": "?",
        # SID, а не имя: имена учётных записей локализованы («СИСТЕМА»).
        "owner_sid": "",
        "admins_can_delete": None,
        "note": "",
        # Права, заданные вручную: явные (не унаследованные) записи или
        # отключённое наследование. Так установщик готовит каталог, в который
        # потом пишет программа без прав администратора.
        "custom_acl": None,
    }
    try:
        sd = win32security.GetFileSecurity(
            path,
            win32security.OWNER_SECURITY_INFORMATION | win32security.DACL_SECURITY_INFORMATION,
        )
    except pywintypes.error as exc:
        info["note"] = f"ACL недоступен: {exc.strerror} (код {exc.winerror})"
        return info

    owner = sd.GetSecurityDescriptorOwner()
    if owner is not None:
        info["owner_sid"] = win32security.ConvertSidToStringSid(owner)
    try:
        name, domain, _ = win32security.LookupAccountSid(None, owner)
        info["owner"] = f"{domain}\\{name}" if domain else name
    except pywintypes.error:
        info["owner"] = info["owner_sid"] or "?"

    admins = win32security.ConvertStringSidToSid("S-1-5-32-544")
    delete_mask = win32con.DELETE | win32con.GENERIC_ALL | 0x1F01FF  # FILE_ALL_ACCESS
    dacl = sd.GetSecurityDescriptorDacl()
    if dacl is None:
        info["admins_can_delete"] = True
        info["custom_acl"] = True
        info["note"] = "DACL отсутствует — доступ у всех"
        return info

    control, _revision = sd.GetSecurityDescriptorControl()
    custom = bool(control & _SE_DACL_PROTECTED)
    allowed = denied = False
    for index in range(dacl.GetAceCount()):
        try:
            (ace_type, flags), mask, sid = dacl.GetAce(index)
        except NotImplementedError:
            # pywin32 не разбирает callback- и object-ACE (типы 5–16). Их
            # ставят только вручную — считаем права заданными явно.
            custom = True
            continue
        if not flags & _INHERITED_ACE:
            custom = True
        if sid != admins or not (mask & delete_mask):
            continue
        if ace_type == win32security.ACCESS_ALLOWED_ACE_TYPE:
            allowed = True
        elif ace_type == win32security.ACCESS_DENIED_ACE_TYPE:
            denied = True
    info["admins_can_delete"] = allowed and not denied
    info["custom_acl"] = custom
    return info


# --- Кто держит файл: Restart Manager -----------------------------------------

_CCH_RM_SESSION_KEY = 32
_CCH_RM_MAX_APP_NAME = 255
_CCH_RM_MAX_SVC_NAME = 63
_ERROR_MORE_DATA = 234


class _RmUniqueProcess(ctypes.Structure):
    _fields_ = [("dwProcessId", wintypes.DWORD), ("ProcessStartTime", wintypes.FILETIME)]


class _RmProcessInfo(ctypes.Structure):
    _fields_ = [
        ("Process", _RmUniqueProcess),
        ("strAppName", wintypes.WCHAR * (_CCH_RM_MAX_APP_NAME + 1)),
        ("strServiceShortName", wintypes.WCHAR * (_CCH_RM_MAX_SVC_NAME + 1)),
        ("ApplicationType", ctypes.c_int),
        ("AppStatus", wintypes.ULONG),
        ("TSSessionId", wintypes.DWORD),
        ("bRestartable", wintypes.BOOL),
    ]


def find_lock_holders(paths: Iterable[str]) -> list[str]:
    """
    Процессы, удерживающие любой из указанных файлов (Restart Manager API).

    Только чтение: сессия Restart Manager лишь опрашивает систему, ничего не
    закрывает и не перезапускает. Возвращает строки вида ``msedge.exe (PID)``.
    """
    files = [str(p) for p in paths]
    if not files or os.name != "nt":
        return []
    try:
        rm = ctypes.WinDLL("rstrtmgr")
    except OSError:
        return []

    handle = wintypes.DWORD()
    key = ctypes.create_unicode_buffer(_CCH_RM_SESSION_KEY + 1)
    if rm.RmStartSession(ctypes.byref(handle), 0, key) != 0:
        return []
    try:
        names = (wintypes.LPCWSTR * len(files))(*files)
        if rm.RmRegisterResources(handle, len(files), names, 0, None, 0, None) != 0:
            return []

        needed = wintypes.UINT(0)
        count = wintypes.UINT(0)
        reasons = wintypes.DWORD(0)
        status = rm.RmGetList(
            handle, ctypes.byref(needed), ctypes.byref(count), None, ctypes.byref(reasons)
        )
        if status == 0 or needed.value == 0:
            return []
        if status != _ERROR_MORE_DATA:
            return []

        buffer = (_RmProcessInfo * needed.value)()
        count = wintypes.UINT(needed.value)
        if (
            rm.RmGetList(
                handle, ctypes.byref(needed), ctypes.byref(count), buffer, ctypes.byref(reasons)
            )
            != 0
        ):
            return []

        holders = []
        for index in range(count.value):
            item = buffer[index]
            name = item.strServiceShortName or item.strAppName or "?"
            holders.append(f"{name} (PID {item.Process.dwProcessId})")
        return holders
    finally:
        rm.RmEndSession(handle)
