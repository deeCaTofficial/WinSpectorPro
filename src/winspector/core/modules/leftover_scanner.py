# src/winspector/core/modules/leftover_scanner.py
"""
Поиск остатков удалённых программ.

После деинсталляции в `%APPDATA%`, `%LOCALAPPDATA%`, `%PROGRAMDATA%` и
`Program Files` остаются каталоги с настройками, кешами и журналами. Найти
их «по имени» нельзя: сотни каталогов в AppData принадлежат играм из Steam,
портативным программам и инструментам разработки, которых нет в списке
установленных программ. Поэтому кандидатом каталог становится только при
**положительной улике** того, что программа была установлена и удалена:

* «призрачная» запись в Uninstall — программа числится, но её каталога и
  исполняемого файла больше нет;
* битый ярлык в меню «Пуск» или на рабочем столе: цель на существующем
  диске отсутствует;
* ключ реестра `HKCU/HKLM\\Software\\<Издатель>\\<Программа>` с путём
  установки, которого больше нет;
* следы запуска (MuiCache, UserAssist, FeatureUsage): Windows помнит, что
  `C:\\Program Files\\Foo\\foo.exe` запускался, а файла больше нет — самый
  частый случай, потому что аккуратный деинсталлятор убирает и запись в
  Uninstall, и ярлыки, но не эти следы;
* каталог в `Program Files`, в котором не осталось ни одного исполняемого
  файла или библиотеки — только журналы и пустые подкаталоги.

И даже с уликой каталог остаётся на месте, если хоть что-то говорит, что
программа жива: имя совпадает с установленной, внутри есть `.exe`, из него
запущен процесс или служба, файлы менялись за последнюю неделю. Всё, что
не прошло проверки, попадает в отчёт как «похоже на остаток, но улик нет»
и не трогается.

Отдельно ищутся **пустые каталоги приложений** (`find_empty_app_dirs`):
деинсталлятор удалил все файлы, а каталог `C:\\Program Files\\Vendor`
оставил. Терять в них нечего, поэтому улика не нужна — но и их удаляет
только последний проход очистки, после удаления файлов.
"""

from __future__ import annotations

import logging
import os
import re
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import psutil

from . import cleanup_engine as engine
from . import installer_leftovers

logger = logging.getLogger(__name__)

try:
    import winreg
except ImportError:  # не Windows
    winreg = None  # type: ignore[assignment]

# Файлы, менявшиеся позже этого срока, означают, что каталогом пользуются.
# Месяц, а не неделя: следы запуска остаются и после переноса программы на
# другой диск, и месяц без изменений — разумная граница «её больше нет».
RECENT_ACTIVITY_DAYS = 30

# Имена исполняемых файлов установщиков: их мёртвый путь ничего не говорит
# о программе.
_INSTALLER_STEMS = ("setup", "install", "unins", "uninst", "update", "updater", "stub", "bootstrap")

# Слова, не несущие смысла при сравнении названий.
_NOISE_WORDS = frozenset(
    {
        "the",
        "inc",
        "ltd",
        "llc",
        "corp",
        "corporation",
        "software",
        "gmbh",
        "co",
        "x64",
        "x86",
        "bit",
        "version",
        "edition",
        "launcher",
        "app",
        "application",
    }
)

# Компоненты путей, которые не могут служить именем программы.
_GENERIC_PATH_PARTS = frozenset(
    {
        "program files",
        "program files x",
        "programs",
        "programdata",
        "appdata",
        "local",
        "roaming",
        "locallow",
        "users",
        "games",
        "bin",
        "app",
        "apps",
        "common",
        "shared",
        "versions",
        "current",
        "windows",
        "system",
        "tools",
        "data",
        "setup",
        "install",
        "temp",
        "cache",
    }
)

# Каталоги AppData/ProgramData, которые никогда не являются остатками
# программ: части системы, каталоги инструментов разработки и кеши,
# которыми занимаются другие правила очистки.
DENY_NAMES = frozenset(
    {
        "microsoft",
        "windows",
        "packages",
        "temp",
        "programs",
        "comms",
        "connecteddevicesplatform",
        "d3dscache",
        "crashdumps",
        "publishers",
        "peerdistrepub",
        "virtualstore",
        "iconcache.db",
        "history",
        "elevateddiagnostics",
        "identitycache",
        "application data",
        "winspectorpro",
        "python",
        "pip",
        "npm",
        "npm-cache",
        "nvm",
        "yarn",
        "pnpm",
        "node-gyp",
        "cargo",
        "rustup",
        "go",
        "docker",
        ".docker",
        "jetbrains",
        "nvidia",
        "nvidia corporation",
        "amd",
        "intel",
        "realtek",
        "onedrive",
        "squirreltemp",
        "package cache",
        "softwaredistribution",
        "usoprivate",
        "usoshared",
        "ssh",
        "regid.1991-06.com.microsoft",
        "microsoft devdiv",
        "windows app certification kit",
        "common files",
        "windowsapps",
        "modifiablewindowsapps",
        "windows nt",
        "windows mail",
        "windows media player",
        "windows photo viewer",
        "windows sidebar",
        "windows defender",
        "windows defender advanced threat protection",
        "windows multimedia platform",
        "windows portable devices",
        "windows security",
        "windowspowershell",
        "internet explorer",
        "msbuild",
        "reference assemblies",
        "dotnet",
        "uninstall information",
        "nuget",
        "microsoft update health tools",
        "rempl",
        "microsoft office",
        "microsoft onedrive",
        "packagemanagement",
        "desktop.ini",
    }
)

# Расширения, наличие которых означает «это установленная программа».
_EXECUTABLE_SUFFIXES = frozenset({".exe", ".dll", ".sys", ".msi"})

# Пустой каталог младше этого срока может ещё заполняться: установщик или
# загрузка только что его создали.
EMPTY_DIR_MIN_AGE_DAYS = 7
# Столько каталогов не бывает в пустом остатке программы; огромное «пустое»
# дерево — чья-то заготовка, а не мусор.
_EMPTY_TREE_MAX_DIRS = 200

# Корни, где каталог программы — это сама установка. Пустой каталог там не
# нужен никому, даже если программа того же издателя стоит в другом месте.
_INSTALL_ROOT_KINDS = frozenset({"program_files", "local_programs"})
# Общесистемные корни: там решают ещё владелец и права каталога.
_MACHINE_ROOT_KINDS = frozenset({"program_files", "programdata"})

_SID_TRUSTED_INSTALLER = "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464"
_SID_SYSTEM = "S-1-5-18"
_FILE_ATTRIBUTE_SYSTEM = 0x4
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400

_DRIVE_PATH = re.compile(r"^[A-Za-z]:\\")
_EXE_IN_COMMAND = re.compile(r"[A-Za-z]:\\[^\"<>|?*]+?\.exe", re.IGNORECASE)


def normalize(name: str) -> str:
    """Каноническое имя: без регистра, версий, пунктуации и служебных слов."""
    text = re.sub(r"\b(x64|x86|64[ -]?bit|32[ -]?bit)\b", " ", str(name).lower())
    text = re.sub(r"\d+(\.\d+)*", " ", text)
    text = re.sub(r"[^a-zа-яё0-9]+", " ", text)
    words = [w for w in text.split() if w not in _NOISE_WORDS]
    return " ".join(words)


def _is_meaningful(alias: str) -> bool:
    """Псевдоним годится для сравнения: не пустой, не общий, не односложный мусор."""
    return len(alias) >= 3 and alias not in _GENERIC_PATH_PARTS and not alias.isdigit()


# --- Модель --------------------------------------------------------------


@dataclass
class Evidence:
    """Улика удаления программы."""

    kind: str  # ghost_uninstall | broken_shortcut | ghost_registry | orphan_install_dir
    scope: str  # machine | user
    title: str  # как показать человеку
    aliases: set[str] = field(default_factory=set)
    detail: str = ""


@dataclass
class LeftoverCandidate:
    """Каталог, похожий на остаток удалённой программы."""

    path: str
    root_kind: str  # appdata | localappdata | locallow | programdata | program_files
    identity: str
    confidence: str  # high — есть улика и проверки пройдены; low — только имя
    evidence: list[str] = field(default_factory=list)
    size_bytes: int = 0
    file_count: int = 0
    newest_mtime: float = 0.0
    kept_reason: str = ""  # почему кандидат с уликой всё же не трогается
    kind: str = "app_data"  # app_data | msi_orphan | package_cache_orphan | squirrel_superseded
    # Убирать ли опустевший родитель после переноса: для `Vendor\App` — да,
    # для `C:\Windows\Installer` и `Package Cache` — никогда.
    prune_parent: bool = True


@dataclass
class InstalledIndex:
    """Всё, что говорит «программа установлена»."""

    aliases: set[str] = field(default_factory=set)
    live_dirs: set[str] = field(default_factory=set)  # каталоги процессов и служб (lower)

    def knows(self, name: str) -> bool:
        return normalize(name) in self.aliases

    def has_live_process_under(self, directory: Path) -> bool:
        base = str(directory).lower().rstrip("\\")
        prefix = base + "\\"
        return any(live == base or live.startswith(prefix) for live in self.live_dirs)


@dataclass
class EmptyAppDir:
    """Каталог приложения, в дереве которого не осталось ни одного файла."""

    path: str
    root_kind: str
    dir_count: int  # каталогов в дереве, включая сам
    newest_mtime: float
    kept_reason: str = ""  # почему пустой каталог всё же остаётся


@dataclass
class LeftoverReport:
    candidates: list[LeftoverCandidate] = field(default_factory=list)
    empty_dirs: list[EmptyAppDir] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)
    ghost_uninstall_entries: list[str] = field(default_factory=list)
    scanned_roots: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)  # какие проверки не выполнялись и почему
    seconds: float = 0.0

    @property
    def actionable(self) -> list[LeftoverCandidate]:
        return [c for c in self.candidates if c.confidence == "high"]

    @property
    def actionable_bytes(self) -> int:
        return sum(c.size_bytes for c in self.actionable)

    @property
    def removable_empty_dirs(self) -> list[EmptyAppDir]:
        return [d for d in self.empty_dirs if not d.kept_reason]


# --- Псевдонимы из путей ------------------------------------------------------


def _known_roots() -> list[str]:
    roots = []
    for var in ("LOCALAPPDATA", "APPDATA", "ProgramData", "ProgramFiles", "ProgramFiles(x86)"):
        value = os.environ.get(var)
        if value:
            roots.append(value.lower().rstrip("\\"))
    local = os.environ.get("LOCALAPPDATA")
    if local:
        roots.append(str(Path(local) / "Programs").lower())
    roots.sort(key=len, reverse=True)
    return roots


def path_aliases(path: str, roots: Iterable[str] | None = None) -> set[str]:
    """
    Псевдонимы программы по пути к её файлу или каталогу.

    Для `C:\\Users\\U\\AppData\\Local\\Roblox\\Versions\\v-1\\RobloxPlayerBeta.exe`
    это `roblox`, `robloxplayerbeta`: первые компоненты после известного
    корня и имя исполняемого файла. Общие слова (`versions`, `bin`) отсекаются.
    """
    aliases: set[str] = set()
    raw = path.strip().strip('"')
    if not raw:
        return aliases
    lowered = raw.lower()
    for root in roots if roots is not None else _known_roots():
        if lowered.startswith(root + "\\"):
            lowered = lowered[len(root) + 1 :]
            break
    else:
        # Путь вне известных корней (например, C:\Games\Foo): убираем диск.
        lowered = re.sub(r"^[a-z]:\\", "", lowered)

    parts = [p for p in lowered.split("\\") if p]
    if not parts:
        return aliases
    last = Path(parts[-1])
    if last.suffix.lower() in (".exe", ".msi", ".lnk"):
        aliases.add(normalize(last.stem))
        parts = parts[:-1]
    for part in parts[:2]:
        aliases.add(normalize(part))
    return {a for a in aliases if _is_meaningful(a)}


# --- Индекс установленного -------------------------------------------------


def build_installed_index() -> InstalledIndex:
    """Собирает всё, что свидетельствует о живых программах. Только чтение."""
    index = InstalledIndex()
    roots = _known_roots()

    for entry in _read_uninstall_entries():
        for name in (entry.get("DisplayName"), entry.get("Publisher")):
            if name:
                index.aliases.add(normalize(name))
        for key in ("InstallLocation", "UninstallString", "DisplayIcon", "InstallSource"):
            value = entry.get(key)
            if value:
                index.aliases.update(path_aliases(_first_path(value), roots))

    # Каталог в Program Files говорит «программа установлена», только если в
    # нём есть что запускать. Пустой каталог или каталог с одними журналами —
    # это как раз то, что остаётся после удаления.
    for directory in _program_dirs():
        try:
            children = [c for c in os.scandir(directory) if _is_plain_dir(c)]
        except OSError:
            continue
        for child in children:
            if _contains_executables(Path(child.path)):
                index.aliases.add(normalize(child.name))

    for process in psutil.process_iter(["exe", "name"]):
        exe = process.info.get("exe")
        if exe:
            index.aliases.update(path_aliases(exe, roots))
            index.live_dirs.add(str(Path(exe).parent).lower())
        name = process.info.get("name")
        if name:
            index.aliases.add(normalize(Path(name).stem))

    for service in psutil.win_service_iter():
        try:
            binpath = service.binpath() or ""
        except (psutil.Error, OSError):
            continue
        match = _EXE_IN_COMMAND.search(binpath)
        if match:
            index.aliases.update(path_aliases(match.group(0), roots))
            index.live_dirs.add(str(Path(match.group(0)).parent).lower())

    for package in _installed_store_packages():
        index.aliases.add(normalize(package.split("_")[0]))

    index.aliases.discard("")
    return index


def _first_path(value: str) -> str:
    """Путь к файлу из команды удаления или значения `DisplayIcon`."""
    match = _EXE_IN_COMMAND.search(value)
    if match:
        return match.group(0)
    return value.split(",")[0].strip().strip('"')


def _program_dirs() -> list[str]:
    dirs = []
    for var in ("ProgramFiles", "ProgramFiles(x86)"):
        value = os.environ.get(var)
        if value:
            dirs.append(value)
    local = os.environ.get("LOCALAPPDATA")
    if local:
        dirs.append(str(Path(local) / "Programs"))
    return [d for d in dirs if Path(d).is_dir()]


def _read_uninstall_entries() -> list[dict[str, Any]]:
    """Записи Programs and Features из всех трёх веток реестра."""
    if winreg is None:
        return []
    entries: list[dict[str, Any]] = []
    locations = [
        (
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
            "machine",
        ),
        (
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Wow6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
            "machine",
        ),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall", "user"),
    ]
    values = ("DisplayName", "Publisher", "InstallLocation", "UninstallString", "DisplayIcon")
    for hive, path, scope in locations:
        try:
            key = winreg.OpenKey(hive, path)
        except OSError:
            continue
        with key:
            for index in range(winreg.QueryInfoKey(key)[0]):
                try:
                    name = winreg.EnumKey(key, index)
                    with winreg.OpenKey(key, name) as sub:
                        entry: dict[str, Any] = {"key": name, "scope": scope}
                        for value in values:
                            try:
                                data = winreg.QueryValueEx(sub, value)[0]
                            except OSError:
                                continue
                            if isinstance(data, str) and data.strip():
                                entry[value] = data.strip()
                        if entry.get("DisplayName"):
                            entries.append(entry)
                except OSError:
                    continue
    return entries


def _installed_store_packages() -> list[str]:
    """Полные имена пакетов Store текущего пользователя — из реестра, без PowerShell."""
    if winreg is None:
        return []
    path = (
        r"Software\Classes\Local Settings\Software\Microsoft\Windows"
        r"\CurrentVersion\AppModel\Repository\Packages"
    )
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
            return [winreg.EnumKey(key, i) for i in range(winreg.QueryInfoKey(key)[0])]
    except OSError:
        return []


# --- Улики удаления ------------------------------------------------------------


def collect_evidence(index: InstalledIndex) -> tuple[list[Evidence], list[str]]:
    """Улики того, что программа была и исчезла. Только чтение."""
    roots = _known_roots()
    evidence: list[Evidence] = []
    ghosts: list[str] = []

    # 1. Призрачные записи Uninstall. Нужен именно `InstallLocation`:
    # у MSI-пакетов и бандлов (VC++ Redistributable) его нет, а путь к
    # деинсталлятору в Package Cache часто вычищен — сам пакет при этом жив.
    for entry in _read_uninstall_entries():
        location = (entry.get("InstallLocation") or "").strip('"').rstrip("\\")
        if not location or not _DRIVE_PATH.match(location):
            continue
        exe = _first_path(entry.get("UninstallString") or "")
        icon = _first_path(entry.get("DisplayIcon") or "")
        checks = [p for p in (location, exe, icon) if p and _DRIVE_PATH.match(p)]
        if not all(_missing_on_present_drive(p) for p in checks):
            continue
        # Псевдонимы только уровня программы: издатель общий для многих
        # продуктов, и по нему кандидат не выбирается.
        aliases = {normalize(entry["DisplayName"])}
        for candidate in checks:
            aliases.update(path_aliases(candidate, roots))
        title = f"запись «{entry['DisplayName']}» в Programs and Features без файлов"
        evidence.append(
            Evidence("ghost_uninstall", entry["scope"], title, aliases, detail=location or exe)
        )
        ghosts.append(entry["DisplayName"])

    # 2. Битые ярлыки.
    for shortcut, target, scope in _broken_shortcuts():
        aliases = {normalize(shortcut.stem)} | path_aliases(target, roots)
        title = f"битый ярлык «{shortcut.stem}» → {target}"
        evidence.append(Evidence("broken_shortcut", scope, title, aliases, detail=str(shortcut)))

    # 3. Ключи реестра издателей с мёртвыми путями.
    for vendor, app, dead_path, scope in _ghost_registry_paths():
        aliases = {normalize(app)} | path_aliases(dead_path, roots)
        title = f"ключ реестра {vendor}\\{app} указывает на отсутствующий {dead_path}"
        evidence.append(Evidence("ghost_registry", scope, title, aliases, detail=dead_path))

    # 4. Следы запуска исполняемых файлов, которых больше нет.
    for source, dead_path, scope in _dead_execution_traces():
        aliases = path_aliases(dead_path, roots)
        title = f"{source}: запускался {dead_path}, файла больше нет"
        evidence.append(Evidence("execution_trace", scope, title, aliases, detail=dead_path))

    # Улика не должна указывать на живую программу: битый ярлык на старую
    # версию Roblox при установленном Roblox — не улика.
    kept = []
    for item in evidence:
        item.aliases = {a for a in item.aliases if _is_meaningful(a)}
        if item.aliases and not any(a in index.aliases for a in item.aliases):
            kept.append(item)
    return kept, ghosts


def _looks_like_install_path(path: str) -> bool:
    """
    Путь похож на место установки программы, а не на загрузку или временный
    файл: установщики в Downloads/Temp/Package Cache не говорят о том, что
    программа удалена.
    """
    lowered = path.lower()
    profile = (os.environ.get("USERPROFILE") or "").lower()
    local = (os.environ.get("LOCALAPPDATA") or "").lower()
    if any(
        marker in lowered
        for marker in ("\\temp\\", "package cache", "\\downloads\\", "\\onedrive\\")
    ):
        return False
    if profile and lowered.startswith(profile + "\\"):
        # Внутри профиля установкой считается только LocalAppData\Programs.
        return bool(local) and lowered.startswith(local.lower() + "\\programs\\")
    stem = Path(lowered).stem
    return not any(word in stem for word in _INSTALLER_STEMS)


def _scope_of_path(path: str) -> str:
    """
    Чьё это удаление: `user`, если путь внутри профиля, иначе `machine`.

    Program Files и корни дисков общие для всех пользователей: если файла
    там нет, программы нет ни у кого — и её каталоги в ProgramData тоже
    остатки.
    """
    profile = (os.environ.get("USERPROFILE") or "").lower()
    return "user" if profile and path.lower().startswith(profile + "\\") else "machine"


def _missing_on_present_drive(path: str) -> bool:
    """Путь отсутствует, хотя его диск подключён (не «отключённая флешка»)."""
    drive = os.path.splitdrive(path)[0]
    if not drive or not Path(drive + "\\").exists():
        return False
    return not Path(path).exists()


def _broken_shortcuts() -> list[tuple[Path, str, str]]:
    """Ярлыки на исполняемые файлы, которых нет на подключённом диске."""
    try:
        import pythoncom
        from win32com.shell import shell
    except ImportError:
        return []

    locations = [
        (r"%APPDATA%\Microsoft\Windows\Start Menu\Programs", "user"),
        (r"%USERPROFILE%\Desktop", "user"),
        (r"%PROGRAMDATA%\Microsoft\Windows\Start Menu\Programs", "machine"),
        (r"%PUBLIC%\Desktop", "machine"),
    ]
    pythoncom.CoInitialize()
    broken: list[tuple[Path, str, str]] = []
    try:
        for raw, scope in locations:
            root = Path(os.path.expandvars(raw))
            if not root.is_dir():
                continue
            for link_path in root.rglob("*.lnk"):
                target = _shortcut_target(link_path, pythoncom, shell)
                if not target or not target.lower().endswith(".exe"):
                    continue
                if _DRIVE_PATH.match(target) and _missing_on_present_drive(target):
                    broken.append(
                        (link_path, target, _scope_of_path(target) if scope == "user" else scope)
                    )
    finally:
        pythoncom.CoUninitialize()
    return broken


def _shortcut_target(link_path: Path, pythoncom: Any, shell: Any) -> str:
    try:
        link = pythoncom.CoCreateInstance(
            shell.CLSID_ShellLink, None, pythoncom.CLSCTX_INPROC_SERVER, shell.IID_IShellLink
        )
        link.QueryInterface(pythoncom.IID_IPersistFile).Load(str(link_path))
        target, _ = link.GetPath(shell.SLGP_RAWPATH)
    except Exception:
        return ""
    return os.path.expandvars(target) if target else ""


_REGISTRY_SKIP = frozenset(
    {
        "microsoft",
        "classes",
        "policies",
        "wow6432node",
        "registeredapplications",
        "clients",
        "google",
        "mozilla",
        "intel",
        "nvidia corporation",
        "amd",
        "realtek",
        "odbc",
        "khronos",
        "wsl",
        "python",
        "javasoft",
        "oracle",
    }
)


def _ghost_registry_paths() -> list[tuple[str, str, str, str]]:
    """Значения-пути в `Software\\<Издатель>\\<Программа>`, ведущие в никуда."""
    if winreg is None:
        return []
    results: list[tuple[str, str, str, str]] = []
    hives = [
        (winreg.HKEY_CURRENT_USER, r"Software"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\Wow6432Node"),
    ]
    for hive, base in hives:
        try:
            root = winreg.OpenKey(hive, base)
        except OSError:
            continue
        with root:
            for vendor in _subkeys(root):
                if vendor.lower() in _REGISTRY_SKIP:
                    continue
                try:
                    vendor_key = winreg.OpenKey(root, vendor)
                except OSError:
                    continue
                with vendor_key:
                    for app in [vendor, *_subkeys(vendor_key)]:
                        key = vendor_key if app == vendor else None
                        try:
                            key = key or winreg.OpenKey(vendor_key, app)
                        except OSError:
                            continue
                        dead = _dead_path_value(key)
                        if key is not vendor_key:
                            key.Close()
                        if dead:
                            # Ветка HKCU может указывать на общесистемный путь —
                            # решает расположение пути, а не куст реестра.
                            results.append((vendor, app, dead, _scope_of_path(dead)))
    return results


def _dead_execution_traces() -> list[tuple[str, str, str]]:
    """
    Пути исполняемых файлов из следов запуска, которых больше нет на диске.

    MuiCache и FeatureUsage хранят пути открытым текстом, UserAssist — в
    ROT13. Все три переживают удаление программы.
    """
    if winreg is None:
        return []
    import codecs

    found: dict[str, str] = {}

    def consider(source: str, raw: str) -> None:
        path = raw.strip()
        if not _DRIVE_PATH.match(path) or not path.lower().endswith(".exe"):
            return
        if path.lower() in found:
            return
        if _looks_like_install_path(path) and _missing_on_present_drive(path):
            found[path.lower()] = source

    mui = r"Software\Classes\Local Settings\Software\Microsoft\Windows\Shell\MuiCache"
    for name, _data, _kind in _values(winreg.HKEY_CURRENT_USER, mui):
        if name.lower().endswith(".friendlyappname"):
            consider("MuiCache", name[: -len(".FriendlyAppName")])

    switched = r"Software\Microsoft\Windows\CurrentVersion\Explorer\FeatureUsage\AppSwitched"
    for name, _data, _kind in _values(winreg.HKEY_CURRENT_USER, switched):
        consider("FeatureUsage", name)

    assist = r"Software\Microsoft\Windows\CurrentVersion\Explorer\UserAssist"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, assist) as root:
            for guid in _subkeys(root):
                for name, _data, _kind in _values(
                    winreg.HKEY_CURRENT_USER, f"{assist}\\{guid}\\Count"
                ):
                    consider("UserAssist", codecs.decode(name, "rot13"))
    except OSError:
        pass

    return [(source, path, _scope_of_path(path)) for path, source in found.items()]


def _values(hive: int, path: str) -> list[tuple[str, Any, int]]:
    try:
        with winreg.OpenKey(hive, path) as key:
            return [winreg.EnumValue(key, i) for i in range(winreg.QueryInfoKey(key)[1])]
    except OSError:
        return []


def _subkeys(key: Any) -> list[str]:
    try:
        return [winreg.EnumKey(key, i) for i in range(winreg.QueryInfoKey(key)[0])]
    except OSError:
        return []


def _dead_path_value(key: Any, limit: int = 40) -> str:
    """Первое строковое значение вида `X:\\...`, которого нет на диске."""
    try:
        count = min(winreg.QueryInfoKey(key)[1], limit)
        for index in range(count):
            name, data, kind = winreg.EnumValue(key, index)
            if kind not in (winreg.REG_SZ, winreg.REG_EXPAND_SZ) or not isinstance(data, str):
                continue
            value = os.path.expandvars(data.strip())
            match = _EXE_IN_COMMAND.search(value)
            value = match.group(0) if match else value.strip('"')
            if not _DRIVE_PATH.match(value) or len(value) < 8:
                continue
            # Интересуют только пути установки, а не произвольные файлы.
            lowered = name.lower()
            if not any(word in lowered for word in ("install", "path", "dir", "location", "exe")):
                continue
            if _looks_like_install_path(value) and _missing_on_present_drive(value.rstrip("\\")):
                return value
    except OSError:
        pass
    return ""


# --- Кандидаты -------------------------------------------------------------


def scan_leftovers(
    index: InstalledIndex | None = None,
    evidence: list[Evidence] | None = None,
    *,
    roots: dict[str, str] | None = None,
    is_safe: Any = None,
    now: float | None = None,
    include_installers: bool | None = None,
) -> LeftoverReport:
    """
    Находит каталоги-остатки. Ничего не удаляет.

    Args:
        index: индекс установленного (по умолчанию собирается с системы).
        evidence: улики (по умолчанию собираются с системы).
        roots: корни для обхода `{kind: path}`; по умолчанию — AppData,
            LocalAppData, LocalLow, ProgramData, Program Files.
        is_safe: `SmartCleaner.is_safe_to_delete`, если нужен общий барьер.
        include_installers: искать ли остатки установщиков (`installer_leftovers`);
            по умолчанию — только при обходе настоящей системы (без `roots`).
    """
    started = time.perf_counter()
    report = LeftoverReport()
    if include_installers is None:
        include_installers = roots is None
    index = index if index is not None else build_installed_index()
    if evidence is None:
        evidence, report.ghost_uninstall_entries = collect_evidence(index)
    report.evidence = evidence
    default_roots = roots is None
    roots = roots if roots is not None else _default_roots()
    now = now if now is not None else time.time()

    alias_evidence: dict[str, list[Evidence]] = {}
    for item in evidence:
        for alias in item.aliases:
            alias_evidence.setdefault(alias, []).append(item)
    # Многословные имена каталогов (`io.github.foo.hyprism-updater`) ловятся
    # по целому слову, если оно достаточно длинное, чтобы не быть общим.
    token_evidence: dict[str, list[Evidence]] = {}
    for alias, items in alias_evidence.items():
        if " " not in alias and len(alias) >= 6:
            token_evidence[alias] = items

    for kind, root in roots.items():
        if not Path(root).is_dir():
            continue
        report.scanned_roots.append(root)
        for directory in _candidate_dirs(root):
            candidate = _evaluate(
                directory, kind, index, alias_evidence, token_evidence, is_safe, now
            )
            if candidate is not None:
                report.candidates.append(candidate)

    empty_roots = dict(roots)
    if default_roots:
        local = os.environ.get("LOCALAPPDATA")
        if local:
            empty_roots["local_programs"] = str(Path(local) / "Programs")
    report.empty_dirs = find_empty_app_dirs(empty_roots, index, is_safe=is_safe, now=now)

    if include_installers:
        findings, notes = installer_leftovers.collect(index.live_dirs, now=now)
        report.notes += notes
        for finding in findings:
            report.candidates.append(
                LeftoverCandidate(
                    path=finding.path,
                    root_kind="installer",
                    identity=Path(finding.path).name,
                    confidence="high",
                    evidence=[finding.reason],
                    size_bytes=finding.size_bytes,
                    file_count=finding.file_count,
                    kind=finding.kind,
                    prune_parent=False,
                )
            )

    report.candidates.sort(key=lambda c: (c.confidence != "high", -c.size_bytes))
    report.seconds = time.perf_counter() - started
    return report


def _default_roots() -> dict[str, str]:
    roots: dict[str, str] = {}
    local = os.environ.get("LOCALAPPDATA")
    if local:
        roots["localappdata"] = local
        roots["locallow"] = str(Path(local).parent / "LocalLow")
    for kind, var in (
        ("appdata", "APPDATA"),
        ("programdata", "ProgramData"),
        ("program_files", "ProgramFiles"),
        ("program_files", "ProgramFiles(x86)"),
    ):
        value = os.environ.get(var)
        if value:
            roots[f"{kind}:{var}" if kind in roots else kind] = value
    return roots


def _candidate_dirs(root: str) -> list[Path]:
    r"""
    Каталоги первого уровня; каталог-издатель раскрывается до второго.

    Каталог, в котором нет файлов, а только подкаталоги (`Adobe\Photoshop`,
    `CD Projekt RED\REDlauncher`), считается каталогом издателя: кандидатами
    становятся его дети по отдельности, а не он сам — у того же издателя
    может стоять другая программа, и её данные трогать нельзя.
    """
    result: list[Path] = []
    try:
        top = [e for e in os.scandir(root) if _is_plain_dir(e)]
    except OSError:
        return result
    for entry in top:
        if entry.name.lower() in DENY_NAMES:
            continue
        try:
            children = list(os.scandir(entry.path))
        except OSError:
            continue
        if children and all(_is_plain_dir(c) for c in children):
            result.extend(Path(c.path) for c in children if c.name.lower() not in DENY_NAMES)
        else:
            result.append(Path(entry.path))
    return result


def _is_plain_dir(entry: os.DirEntry[str]) -> bool:
    try:
        return (
            entry.is_dir(follow_symlinks=False)
            and not entry.is_symlink()
            and not entry.is_junction()
        )
    except OSError:
        return False


def _evaluate(
    directory: Path,
    root_kind: str,
    index: InstalledIndex,
    alias_evidence: dict[str, list[Evidence]],
    token_evidence: dict[str, list[Evidence]],
    is_safe: Any,
    now: float,
) -> LeftoverCandidate | None:
    name = normalize(directory.name)
    if not _is_meaningful(name):
        return None
    root_kind = root_kind.split(":")[0]

    # Установленное не трогаем.
    if name in index.aliases:
        return None

    matched = list(alias_evidence.get(name) or [])
    if not matched and " " in name:
        tokens = set(name.split())
        if not tokens & index.aliases:
            for token in tokens:
                matched.extend(token_evidence.get(token) or [])
    if root_kind in ("programdata", "program_files"):
        # Общесистемные каталоги требуют улик уровня машины: программа могла
        # остаться установленной у другого пользователя.
        matched = [e for e in matched if e.scope == "machine"]

    if root_kind == "program_files" and not matched:
        orphan = _orphan_install_dir(directory, index, now)
        if orphan is None:
            return None
        matched = [orphan]

    if not matched:
        return None

    candidate = LeftoverCandidate(
        path=str(directory),
        root_kind=root_kind,
        identity=directory.name,
        confidence="high",
        evidence=[e.title for e in matched],
    )
    scan = engine.process_tree(directory, mode="scan")
    candidate.size_bytes = scan.freed_bytes
    candidate.file_count = scan.freed_files
    candidate.newest_mtime = _newest_mtime(directory)
    if candidate.file_count == 0:
        # Пустым каталогам карантин не нужен: восстанавливать в них нечего.
        # Их проверяет и убирает `find_empty_app_dirs` — последним шагом,
        # после удаления файлов.
        return None

    candidate.kept_reason = _keep_reason(
        directory, root_kind, index, is_safe, candidate.newest_mtime, now
    )
    if candidate.kept_reason:
        candidate.confidence = "low"
    return candidate


def _keep_reason(
    directory: Path, root_kind: str, index: InstalledIndex, is_safe: Any, newest: float, now: float
) -> str:
    """Почему каталог с уликой всё равно остаётся на месте."""
    if is_safe is not None and not is_safe(directory):
        return "путь под защитой"
    if index.has_live_process_under(directory):
        return "из каталога запущен процесс или служба"
    if root_kind != "program_files" and _contains_executables(directory):
        return "внутри есть исполняемые файлы — похоже на портативную программу"
    if newest and now - newest < RECENT_ACTIVITY_DAYS * 86400:
        return f"файлы менялись за последние {RECENT_ACTIVITY_DAYS} дней"
    return ""


def _orphan_install_dir(directory: Path, index: InstalledIndex, now: float) -> Evidence | None:
    """
    Каталог в Program Files без единого exe/dll — сам себе улика.

    Каталоги Microsoft (SDK, .NET, NuGet) исключены: там XML-списки и
    конфигурация без исполняемых файлов — рабочие данные Visual Studio.
    Подкаталог издателя считается остатком, только если исполняемых файлов
    нет во всём каталоге издателя: иначе это часть живой программы.
    """
    if directory.name.lower().startswith("microsoft"):
        return None
    if index.has_live_process_under(directory) or _contains_executables(directory):
        return None
    if engine.describe_access(str(directory)).get("owner_sid") == _SID_TRUSTED_INSTALLER:
        return None  # компонент Windows, а не программа
    parent = directory.parent
    if parent.name.lower().startswith("microsoft"):
        return None
    if normalize(parent.name) not in _GENERIC_PATH_PARTS and (
        index.has_live_process_under(parent) or _contains_executables(parent)
    ):
        return None
    return Evidence(
        "orphan_install_dir",
        "machine",
        f"в {directory} не осталось исполняемых файлов и библиотек",
        {normalize(directory.name)},
        detail=str(directory),
    )


def _contains_executables(directory: Path, limit: int = 20000) -> bool:
    seen = 0
    stack = [str(directory)]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    seen += 1
                    if seen > limit:
                        return True  # слишком большой каталог — считаем живым
                    try:
                        if entry.is_symlink() or entry.is_junction():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                        elif Path(entry.name).suffix.lower() in _EXECUTABLE_SUFFIXES:
                            return True
                    except OSError:
                        continue
        except OSError:
            continue
    return False


def _newest_mtime(directory: Path, limit: int = 20000) -> float:
    newest = 0.0
    seen = 0
    stack = [str(directory)]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    seen += 1
                    if seen > limit:
                        return time.time()  # огромный каталог — считаем активным
                    try:
                        if entry.is_symlink() or entry.is_junction():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                        else:
                            newest = max(newest, entry.stat(follow_symlinks=False).st_mtime)
                    except OSError:
                        continue
        except OSError:
            continue
    return newest


# --- Пустые каталоги приложений ----------------------------------------------


def find_empty_app_dirs(
    roots: dict[str, str],
    index: InstalledIndex,
    *,
    is_safe: Any = None,
    now: float | None = None,
    access: Callable[[str], dict[str, Any]] | None = None,
) -> list[EmptyAppDir]:
    r"""
    Каталоги приложений, в дереве которых нет ни одного файла. Только чтение.

    Деинсталляторы удаляют файлы, но оставляют сам каталог: `C:\Program
    Files\Hypixel Studios`, `C:\Program Files\Epic Games`. Терять в таком
    каталоге нечего, поэтому улика удаления программы не нужна; каталог
    остаётся, только если:

    * в дереве есть хоть один файл, ссылка или системный каталог;
    * он менялся или создан за последние `EMPTY_DIR_MIN_AGE_DAYS` дней —
      установщик или загрузка могли только что его создать;
    * в `AppData` и `ProgramData` его имя совпадает с установленной
      программой — она может рассчитывать, что каталог существует;
    * в `Program Files` и `ProgramData` он принадлежит TrustedInstaller
      (компонент Windows), в `ProgramData` — ещё и SYSTEM (создан службой),
      или права на него заданы вручную: так установщик готовит каталог, в
      который программа потом пишет без прав администратора, и сама она его
      уже не создаст.

    Каталог с правами по наследованию программа без прав администратора в
    `Program Files` создать и заполнить не может — значит, её установщик
    работает с повышенными правами и создаст каталог заново, если он понадобится.

    Смотрятся каталоги верхнего уровня; в `Program Files` — ещё и дети каталогов
    издателя (`Microsoft Visual Studio\2019`). Кандидаты, оставленные на месте,
    тоже попадают в список — с причиной в `kept_reason`.
    """
    now = now if now is not None else time.time()
    access = access or engine.describe_access
    found: list[EmptyAppDir] = []
    for key, root in roots.items():
        kind = key.split(":")[0]
        for directory, (dir_count, newest), label in _empty_dir_candidates(
            root, expand_vendors=kind in _INSTALL_ROOT_KINDS
        ):
            if not _is_meaningful(normalize(label)):
                continue  # «Common», «2019» без издателя — не имя программы
            item = EmptyAppDir(str(directory), kind, dir_count, newest)
            item.kept_reason = _empty_dir_keep_reason(
                directory, kind, index, is_safe, newest, now, access
            )
            found.append(item)
    return found


def empty_tree(directory: Path, limit: int = _EMPTY_TREE_MAX_DIRS) -> tuple[int, float] | None:
    """
    `(каталогов, самое позднее изменение или создание)` для дерева без файлов.

    None, если в дереве есть файл, ссылка или точка повторного анализа,
    системный каталог, что-то не читается или каталогов больше `limit`:
    такое дерево пустым не считается.
    """
    dirs = 0
    newest = 0.0
    stack = [str(directory)]
    while stack:
        current = stack.pop()
        dirs += 1
        if dirs > limit:
            return None
        try:
            info = Path(current).stat(follow_symlinks=False)
            attributes = getattr(info, "st_file_attributes", 0)
            if attributes & (_FILE_ATTRIBUTE_SYSTEM | _FILE_ATTRIBUTE_REPARSE_POINT):
                return None
            newest = max(newest, info.st_mtime, getattr(info, "st_birthtime", 0.0))
            with os.scandir(current) as entries:
                for entry in entries:
                    if not _is_plain_dir(entry):
                        return None
                    stack.append(entry.path)
        except OSError:
            return None
    return dirs, newest


def _empty_dir_candidates(
    root: str, *, expand_vendors: bool
) -> Iterable[tuple[Path, tuple[int, float], str]]:
    """`(каталог, итог empty_tree, имя для проверки)` для пустых деревьев под `root`."""
    try:
        top = [e for e in os.scandir(root) if _is_plain_dir(e)]
    except OSError:
        return
    for entry in top:
        if entry.name.lower() in DENY_NAMES:
            continue
        tree = empty_tree(Path(entry.path))
        if tree is not None:
            yield Path(entry.path), tree, entry.name
            continue
        if not expand_vendors:
            continue
        try:
            children = list(os.scandir(entry.path))
        except OSError:
            continue
        if not children or not all(_is_plain_dir(c) for c in children):
            continue  # не каталог издателя: внутри есть файлы
        for child in children:
            if child.name.lower() in DENY_NAMES:
                continue
            tree = empty_tree(Path(child.path))
            if tree is not None:
                # Имя проверяется по издателю: у `Vendor\2019` своего нет.
                yield Path(child.path), tree, entry.name


def _empty_dir_keep_reason(
    directory: Path,
    kind: str,
    index: InstalledIndex,
    is_safe: Any,
    newest: float,
    now: float,
    access: Callable[[str], dict[str, Any]],
) -> str:
    """Почему пустой каталог всё же остаётся; пустая строка — можно убрать."""
    if now - newest < EMPTY_DIR_MIN_AGE_DAYS * 86400:
        return f"создан или менялся за последние {EMPTY_DIR_MIN_AGE_DAYS} дней"
    if is_safe is not None and not is_safe(directory):
        return "путь под защитой"
    if index.has_live_process_under(directory):
        return "на каталог указывает процесс или служба"
    if kind not in _INSTALL_ROOT_KINDS and _mentions_installed(directory.name, index):
        return "имя совпадает с установленной программой"
    if kind in _MACHINE_ROOT_KINDS:
        acl = access(str(directory))
        owner = acl.get("owner_sid", "")
        if owner == _SID_TRUSTED_INSTALLER:
            return "компонент Windows (владелец TrustedInstaller)"
        if kind == "programdata" and owner == _SID_SYSTEM:
            return "создан службой (владелец SYSTEM)"
        if acl.get("custom_acl") is None:
            return "права на каталог не читаются"
        if acl["custom_acl"]:
            return "права заданы вручную — каталог подготовлен установщиком"
    return ""


def _mentions_installed(name: str, index: InstalledIndex) -> bool:
    """Имя или его заметное слово — псевдоним установленной программы."""
    normalized = normalize(name)
    if normalized in index.aliases:
        return True
    return any(len(token) >= 4 and token in index.aliases for token in normalized.split())
