# src/winspector/core/modules/installer_leftovers.py
"""
Остатки установщиков: то, что установщики оставляют после обновлений и
удаления программ и что сами уже никогда не уберут.

* **Осиротевшие пакеты Windows Installer.** `%WINDIR%\\Installer` хранит
  копию `.msi`/`.msp` каждого установленного продукта и патча — без неё не
  работают восстановление и удаление. Но Office Click-to-Run, NVIDIA Nsight и
  многие другие оставляют там копии после каждого обновления. Сиротой пакет
  считается, только если его не называет своим ни один продукт и ни один
  патч **ни одного пользователя** (MSI API). Без прав администратора полный
  список недоступен — тогда поиск не выполняется вовсе.
* **Осиротевший Package Cache.** Установщики на WiX Burn (.NET, VC++, Visual
  Studio) кешируют пакеты в `Package Cache\\{GUID}v<версия>`. Каталог — сирота,
  если GUID не установлен ни как MSI-продукт, ни как пакет-bundle, на него
  не ссылаются записи удаления и зависимости.
* **Вытесненные версии Squirrel.** Discord, Figma, Slack и другие приложения
  на Squirrel ставят обновление в новый `app-<версия>` и оставляют предыдущий
  каталог. Старая версия — остаток, если новая стоит больше недели и из
  старой не запущен ни один процесс.

Ничего не удаляется: всё найденное уходит в карантин (`quarantine.py`) и
возвращается на место одной командой в течение 30 дней.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

try:
    import winreg
except ImportError:  # не Windows
    winreg = None  # type: ignore[assignment]

# Пакет или каталог младше этого срока не трогается: установщик мог ещё не
# закончить регистрацию, а новая версия программы — не проявить себя.
MIN_AGE_DAYS = 30
SQUIRREL_MIN_AGE_DAYS = 7

_ERROR_NO_MORE_ITEMS = 259
_ALL_USERS_SID = "s-1-1-0"
_MSIINSTALLCONTEXT_ALL = 7
_MSIPATCHSTATE_ALL = 15

_PACKAGE_CACHE_DIR = re.compile(
    r"^(\{[0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}\})v[\d.]+$"
)
_SQUIRREL_APP_DIR = re.compile(r"^app-(\d+(?:\.\d+)*)$")


@dataclass
class InstallerFinding:
    """Найденный остаток установщика."""

    path: str
    kind: str  # msi_orphan | package_cache_orphan | squirrel_superseded
    reason: str
    size_bytes: int
    file_count: int


@dataclass
class MsiInventory:
    """Всё, что Windows Installer считает установленным, по всем пользователям."""

    product_codes: set[str] = field(default_factory=set)
    local_packages: set[str] = field(default_factory=set)  # полные пути, нижний регистр
    complete: bool = False
    error: str = ""

    @property
    def package_names(self) -> set[str]:
        return {Path(p).name for p in self.local_packages}


# --- MSI API ----------------------------------------------------------------


def read_msi_inventory() -> MsiInventory:
    """
    Продукты и патчи всех пользователей с путями к кешированным пакетам.

    `complete` становится True, только если оба перечисления дошли до конца
    без ошибок. Любая ошибка — например, отказ в доступе без прав
    администратора — означает, что список неполный и искать сирот нельзя.
    """
    inventory = MsiInventory()
    if os.name != "nt":
        inventory.error = "не Windows"
        return inventory
    try:
        msi = ctypes.WinDLL("msi")
    except OSError as exc:
        inventory.error = f"msi.dll недоступна: {exc}"
        return inventory

    code = ctypes.create_unicode_buffer(39)
    context = wintypes.DWORD()
    sid = ctypes.create_unicode_buffer(256)
    index = 0
    while True:
        sid_len = wintypes.DWORD(256)
        rc = msi.MsiEnumProductsExW(
            None, _ALL_USERS_SID, _MSIINSTALLCONTEXT_ALL, index,
            code, ctypes.byref(context), sid, ctypes.byref(sid_len),
        )  # fmt: skip
        if rc == _ERROR_NO_MORE_ITEMS:
            break
        if rc != 0:
            inventory.error = f"перечисление продуктов прервано (код {rc})"
            return inventory
        inventory.product_codes.add(code.value.upper())
        package = _msi_string(
            msi.MsiGetProductInfoExW, code.value, sid.value or None, context.value, "LocalPackage"
        )
        if package:
            inventory.local_packages.add(package.lower())
        index += 1

    patch = ctypes.create_unicode_buffer(39)
    target = ctypes.create_unicode_buffer(39)
    index = 0
    while True:
        sid_len = wintypes.DWORD(256)
        rc = msi.MsiEnumPatchesExW(
            None, _ALL_USERS_SID, _MSIINSTALLCONTEXT_ALL, _MSIPATCHSTATE_ALL, index,
            patch, target, ctypes.byref(context), sid, ctypes.byref(sid_len),
        )  # fmt: skip
        if rc == _ERROR_NO_MORE_ITEMS:
            break
        if rc != 0:
            inventory.error = f"перечисление патчей прервано (код {rc})"
            return inventory
        package = _msi_patch_string(
            msi, patch.value, target.value, sid.value or None, context.value, "LocalPackage"
        )
        if package:
            inventory.local_packages.add(package.lower())
        index += 1

    inventory.complete = bool(inventory.product_codes)
    if not inventory.complete:
        inventory.error = "установленных продуктов не найдено — список недостоверен"
    return inventory


def _msi_string(func, code: str, sid: str | None, context: int, prop: str) -> str:
    size = wintypes.DWORD(1024)
    value = ctypes.create_unicode_buffer(1024)
    if func(code, sid, context, prop, value, ctypes.byref(size)) == 0:
        return value.value
    return ""


def _msi_patch_string(
    msi, patch: str, target: str, sid: str | None, context: int, prop: str
) -> str:
    size = wintypes.DWORD(1024)
    value = ctypes.create_unicode_buffer(1024)
    if msi.MsiGetPatchInfoExW(patch, target, sid, context, prop, value, ctypes.byref(size)) == 0:
        return value.value
    return ""


def msi_subject(path: str) -> str:
    """Название продукта из свойств пакета (Summary Information, Subject)."""
    if os.name != "nt":
        return ""
    try:
        msi = ctypes.WinDLL("msi")
    except OSError:
        return ""
    handle = wintypes.HANDLE()
    if msi.MsiGetSummaryInformationW(None, path, 0, ctypes.byref(handle)) != 0:
        return ""
    try:
        kind, number, stamp = ctypes.c_uint(), ctypes.c_int(), wintypes.FILETIME()
        value, size = ctypes.create_unicode_buffer(512), wintypes.DWORD(512)
        msi.MsiSummaryInfoGetPropertyW(
            handle, 3, ctypes.byref(kind), ctypes.byref(number), ctypes.byref(stamp),
            value, ctypes.byref(size),
        )  # fmt: skip
        return value.value
    finally:
        msi.MsiCloseHandle(handle)


# --- Детекторы (чистые функции: данные системы передаются параметрами) -----


def find_orphaned_msi_packages(
    installer_dir: Path, inventory: MsiInventory, *, now: float | None = None
) -> list[InstallerFinding]:
    """Пакеты `.msi`/`.msp`, которые не числятся ни за одним продуктом или патчем."""
    if not inventory.complete or not installer_dir.is_dir():
        return []
    now = now if now is not None else time.time()
    referenced_paths = inventory.local_packages
    # Сверка и по имени: путь в реестре бывает записан иначе (короткие имена,
    # другой регистр диска). Совпало имя — пакет оставляем.
    referenced_names = inventory.package_names

    candidates: list[os.DirEntry[str]] = []
    total = 0
    for entry in os.scandir(installer_dir):
        try:
            if not entry.is_file(follow_symlinks=False):
                continue
        except OSError:
            continue
        if not entry.name.lower().endswith((".msi", ".msp")):
            continue
        total += 1
        if entry.path.lower() in referenced_paths or entry.name.lower() in referenced_names:
            continue
        candidates.append(entry)

    # Предохранитель: если «сиротами» оказалась большая часть кеша, значит
    # перечисление вернуло неполный список — ничего не предлагаем.
    if total and len(candidates) > total * 0.5:
        logger.warning(
            "Сиротами выглядят %d из %d пакетов установщика — список продуктов неполон, пропуск.",
            len(candidates),
            total,
        )
        return []

    findings: list[InstallerFinding] = []
    for entry in candidates:
        try:
            stat = entry.stat(follow_symlinks=False)
        except OSError:
            continue
        if now - stat.st_mtime < MIN_AGE_DAYS * 86400:
            continue
        subject = msi_subject(entry.path) or entry.name
        findings.append(
            InstallerFinding(
                path=entry.path,
                kind="msi_orphan",
                reason=f"пакет установщика «{subject}» не принадлежит ни одной установленной программе",
                size_bytes=stat.st_size,
                file_count=1,
            )
        )
    return findings


def find_orphaned_package_cache(
    cache_roots: list[Path],
    inventory: MsiInventory,
    registered: RegisteredInstallers,
    *,
    now: float | None = None,
) -> list[InstallerFinding]:
    """Каталоги `Package Cache\\{GUID}v<версия>` удалённых продуктов и bundle."""
    if not inventory.complete:
        return []
    now = now if now is not None else time.time()
    findings: list[InstallerFinding] = []
    for root in cache_roots:
        if not root.is_dir():
            continue
        for entry in os.scandir(root):
            match = _PACKAGE_CACHE_DIR.match(entry.name)
            if not match or not entry.is_dir(follow_symlinks=False):
                continue
            guid = match.group(1).upper()
            if guid in inventory.product_codes or guid in registered.bundle_ids:
                continue
            if guid in registered.dependency_ids:
                continue
            if entry.name.lower() in registered.referenced_text:
                continue
            size, files, newest = _tree_stats(Path(entry.path))
            if not files or now - newest < MIN_AGE_DAYS * 86400:
                continue
            first = next((p.name for p in Path(entry.path).iterdir()), entry.name)
            findings.append(
                InstallerFinding(
                    path=entry.path,
                    kind="package_cache_orphan",
                    reason=f"кеш установщика «{first}»: продукт {guid} больше не установлен",
                    size_bytes=size,
                    file_count=files,
                )
            )
    return findings


def find_superseded_squirrel_versions(
    local_appdata: Path, live_dirs: set[str], *, now: float | None = None
) -> list[InstallerFinding]:
    """Предыдущие `app-<версия>` приложений на Squirrel (рядом лежит Update.exe)."""
    now = now if now is not None else time.time()
    findings: list[InstallerFinding] = []
    if not local_appdata.is_dir():
        return findings
    for app in local_appdata.iterdir():
        try:
            if not app.is_dir() or app.is_junction() or app.is_symlink():
                continue
            if not (app / "Update.exe").is_file():
                continue
            versions = [
                (tuple(int(x) for x in m.group(1).split(".")), d)
                for d in app.iterdir()
                if d.is_dir() and (m := _SQUIRREL_APP_DIR.match(d.name))
            ]
        except OSError:
            continue
        if len(versions) < 2:
            continue
        versions.sort()
        newest = versions[-1][1]
        try:
            newest_age = now - newest.stat().st_mtime
        except OSError:
            continue
        # Новая версия должна проработать неделю: если она окажется сломанной,
        # предыдущая ещё нужна для отката.
        if newest_age < SQUIRREL_MIN_AGE_DAYS * 86400:
            continue
        for _, old in versions[:-1]:
            prefix = str(old).lower().rstrip("\\") + "\\"
            if any(d.startswith(prefix) or d == prefix[:-1] for d in live_dirs):
                continue
            size, files, _ = _tree_stats(old)
            if not files:
                continue
            findings.append(
                InstallerFinding(
                    path=str(old),
                    kind="squirrel_superseded",
                    reason=f"{app.name}: версия {old.name} заменена на {newest.name}",
                    size_bytes=size,
                    file_count=files,
                )
            )
    return findings


# --- Реестр: зарегистрированные bundle и зависимости ------------------------


@dataclass
class RegisteredInstallers:
    bundle_ids: set[str] = field(default_factory=set)
    dependency_ids: set[str] = field(default_factory=set)
    referenced_text: str = ""  # все строки записей удаления, нижний регистр


def read_registered_installers() -> RegisteredInstallers:
    """Записи удаления (в т.ч. bundle Burn) и провайдеры зависимостей."""
    result = RegisteredInstallers()
    if winreg is None:
        return result
    texts: list[str] = []
    uninstall = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
        ),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]
    for hive, path in uninstall:
        for name, values in _iter_subkeys_with_values(hive, path):
            if name.startswith("{"):
                result.bundle_ids.add(name.upper())
            texts.extend(str(v).lower() for v in values.values() if isinstance(v, str))
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for name, values in _iter_subkeys_with_values(
            hive, r"SOFTWARE\Classes\Installer\Dependencies"
        ):
            for text in (name, *(v for v in values.values() if isinstance(v, str))):
                for guid in re.findall(r"\{[0-9A-Fa-f-]{36}\}", text):
                    result.dependency_ids.add(guid.upper())
    result.referenced_text = "\n".join(texts)
    return result


def _iter_subkeys_with_values(hive: int, path: str):
    try:
        root = winreg.OpenKey(hive, path)
    except OSError:
        return
    with root:
        for index in range(winreg.QueryInfoKey(root)[0]):
            try:
                name = winreg.EnumKey(root, index)
                with winreg.OpenKey(root, name) as sub:
                    values = {}
                    for i in range(winreg.QueryInfoKey(sub)[1]):
                        value_name, data, _ = winreg.EnumValue(sub, i)
                        values[value_name] = data
                yield name, values
            except OSError:
                continue


def _tree_stats(root: Path) -> tuple[int, int, float]:
    size = files = 0
    newest = 0.0
    stack = [str(root)]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        if entry.is_symlink() or entry.is_junction():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                            continue
                        stat = entry.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    size += stat.st_size
                    files += 1
                    newest = max(newest, stat.st_mtime)
        except OSError:
            continue
    return size, files, newest


# --- Сборка ------------------------------------------------------------------


def collect(
    live_dirs: set[str], *, now: float | None = None
) -> tuple[list[InstallerFinding], list[str]]:
    """Все остатки установщиков на этой машине и заметки о пропущенных проверках."""
    notes: list[str] = []
    findings: list[InstallerFinding] = []
    windir = Path(os.environ.get("SystemRoot") or os.environ.get("WINDIR") or r"C:\Windows")
    local = os.environ.get("LOCALAPPDATA")
    program_data = os.environ.get("ProgramData") or r"C:\ProgramData"

    inventory = read_msi_inventory()
    if inventory.complete:
        findings += find_orphaned_msi_packages(windir / "Installer", inventory, now=now)
        caches = [Path(program_data) / "Package Cache"]
        if local:
            caches.append(Path(local) / "Package Cache")
        findings += find_orphaned_package_cache(
            caches, inventory, read_registered_installers(), now=now
        )
    else:
        notes.append(
            "Кеш Windows Installer и Package Cache не проверялись: "
            f"{inventory.error or 'список установленных продуктов недоступен'} "
            "(нужны права администратора)."
        )

    if local:
        findings += find_superseded_squirrel_versions(Path(local), live_dirs, now=now)
    return findings, notes
