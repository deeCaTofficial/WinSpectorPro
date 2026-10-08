# src/winspector/core/wmi_workers.py
"""
Функции сбора системной информации через WMI.

Выполняются в отдельном процессе (см. `worker_pool`), поэтому написаны
синхронно и возвращают ошибку значением, а не исключением.

WMI опрашивается через `win32com` напрямую, без пакета `wmi`: тот не
обновлялся с 2021 года, при импорте предупреждает о некорректных escape-
последовательностях (в будущих версиях Python это станет ошибкой) и лишь
оборачивает те же самые COM-вызовы, добавляя около трети ко времени запроса.

Почему без потоков: WMI работает через COM, а COM-объект привязан к
апартаменту создавшего его потока. Прежняя версия раскладывала запросы по
`asyncio.to_thread`, и обращения из чужих потоков падали с
`CO_E_NOTINITIALIZED` (0x800401F0) — сбор данных об оборудовании молча
возвращал пустой результат. Внутри выделенного процесса параллелить нечего:
последовательные запросы и проще, и корректны.
"""

from __future__ import annotations

import contextlib
from typing import Any

try:
    import pythoncom
    import win32com.client
except ImportError:  # окружения без pywin32
    pythoncom = None
    win32com = None

# Тот же моникер, что по умолчанию строил пакет `wmi`.
_CIMV2_MONIKER = "winmgmts:{impersonationLevel=impersonate}!root/cimv2"


class _WmiConnection:
    """Минимальная обёртка над `SWbemServices`: только выполнение WQL."""

    def __init__(self, services: Any) -> None:
        self._services = services

    def query(self, wql: str) -> list[Any]:
        return list(self._services.ExecQuery(wql))


def _init_com() -> None:
    """Инициализирует COM для текущего потока, если это возможно."""
    if pythoncom is None:
        return
    # Повторный вызов в уже инициализированном апартаменте — не ошибка.
    with contextlib.suppress(Exception):
        pythoncom.CoInitialize()


def _get_wmi_connection() -> Any | None:
    """Создаёт соединение с WMI. Возвращает None, если это не удалось."""
    if win32com is None:
        print("WMI connection failed in worker process: pywin32 is not installed")
        return None
    _init_com()
    try:
        return _WmiConnection(win32com.client.GetObject(_CIMV2_MONIKER))
    except Exception as exc:
        print(f"WMI connection failed in worker process: {exc}")
        return None


def _associators(obj: Any, result_class: str) -> list[Any]:
    """Связанные объекты заданного класса (`SWbemObject.Associators_`)."""
    return list(obj.Associators_(strResultClass=result_class))


def _safe_query(wmi_con: Any, wql: str) -> list[Any]:
    """Выполняет WQL-запрос, возвращая пустой список при любой ошибке."""
    try:
        return list(wmi_con.query(wql))
    except Exception as exc:
        print(f"WMI query failed ({wql[:60]}...): {exc}")
        return []


def _collect_hardware(wmi_con: Any) -> dict[str, Any]:
    """Собирает сведения об оборудовании."""
    hardware: dict[str, Any] = {"gpu": [], "disks": []}

    cpus = _safe_query(
        wmi_con,
        "SELECT Name, NumberOfCores, NumberOfLogicalProcessors FROM Win32_Processor",
    )
    if cpus:
        cpu = cpus[0]
        hardware["cpu"] = {
            "name": (cpu.Name or "").strip(),
            "cores": cpu.NumberOfCores,
            "threads": cpu.NumberOfLogicalProcessors,
        }

    for gpu in _safe_query(
        wmi_con,
        "SELECT Name, DriverVersion, AdapterRAM, AdapterCompatibility FROM Win32_VideoController",
    ):
        vendor = gpu.AdapterCompatibility or ""
        if "Microsoft" in vendor:
            continue  # программный адаптер, а не физическая видеокарта
        hardware["gpu"].append(
            {
                "name": (gpu.Name or "").strip(),
                "driver_version": gpu.DriverVersion,
                "vram_mb": round(int(gpu.AdapterRAM) / (1024**2)) if gpu.AdapterRAM else None,
            }
        )

    systems = _safe_query(wmi_con, "SELECT TotalPhysicalMemory FROM Win32_ComputerSystem")
    if systems and systems[0].TotalPhysicalMemory:
        hardware["ram_gb"] = round(int(systems[0].TotalPhysicalMemory) / (1024**3))

    boards = _safe_query(wmi_con, "SELECT Manufacturer, Product FROM Win32_BaseBoard")
    if boards:
        hardware["motherboard"] = {
            "manufacturer": (boards[0].Manufacturer or "").strip(),
            "product": (boards[0].Product or "").strip(),
        }

    for disk in _safe_query(
        wmi_con, "SELECT DeviceID, Model, Size, MediaType FROM Win32_DiskDrive"
    ):
        hardware["disks"].append(
            {
                "model": (disk.Model or "").strip(),
                "size_gb": round(int(disk.Size) / (1024**3)) if disk.Size else None,
                "media_type": disk.MediaType,
                "partitions": _collect_partitions(disk),
            }
        )

    return hardware


def _collect_partitions(disk: Any) -> list[dict[str, Any]]:
    """
    Связывает физический диск с логическими томами.

    Используется `Associators_` самого COM-объекта: он корректно строит путь.
    Ручной WQL требовал экранирования `\\\\.\\PHYSICALDRIVE0` и возвращал
    WBEM_E_NOT_FOUND.
    """
    partitions: list[dict[str, Any]] = []

    try:
        associated = _associators(disk, "Win32_DiskPartition")
    except Exception as exc:
        print(f"Disk partition lookup failed: {exc}")
        return partitions

    for part in associated:
        try:
            logical_disks = _associators(part, "Win32_LogicalDisk")
        except Exception:
            continue
        for logical in logical_disks:
            partitions.append(
                {
                    "drive_letter": logical.DeviceID,
                    "volume_name": logical.VolumeName,
                    "file_system": logical.FileSystem,
                    "free_space_gb": (
                        round(int(logical.FreeSpace) / (1024**3)) if logical.FreeSpace else None
                    ),
                }
            )
    return partitions


def get_hardware_info_worker() -> dict[str, Any]:
    """Точка входа воркера: сведения об оборудовании."""
    wmi_con = _get_wmi_connection()
    if wmi_con is None:
        return {"error": "WMI connection failed."}
    try:
        return _collect_hardware(wmi_con)
    except Exception as exc:
        return {"error": f"Hardware collection failed: {exc}"}


def get_running_processes_worker() -> dict[str, Any]:
    """Точка входа воркера: пользовательские процессы (без системных учёток)."""
    wmi_con = _get_wmi_connection()
    if wmi_con is None:
        return {"error": "WMI connection failed."}

    system_accounts = {"local system", "system", "network service", "local service"}
    processes: list[dict[str, Any]] = []

    try:
        rows = wmi_con.query(
            "SELECT ProcessId, Name, ExecutablePath, CommandLine FROM Win32_Process"
        )
    except Exception as exc:
        return {"error": str(exc)}

    for process in rows:
        try:
            owner_info = process.GetOwner()
            owner_domain = (owner_info[2] or "").lower()
            owner_user = (owner_info[0] or "").lower()
            if owner_domain in system_accounts or owner_user in system_accounts:
                continue
            processes.append(
                {
                    "pid": process.ProcessId,
                    "name": process.Name,
                    "path": process.ExecutablePath,
                    "command_line": process.CommandLine,
                    "owner": f"{owner_domain}\\{owner_user}",
                }
            )
        except Exception:
            continue

    return {"processes": processes}
