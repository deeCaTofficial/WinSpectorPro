# src/winspector/core/wmi_workers.py
"""
Функции сбора системной информации через WMI.

Выполняются в отдельном процессе (см. `worker_pool`), поэтому написаны
синхронно и возвращают ошибку значением, а не исключением.

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

import wmi

try:
    import pythoncom
except ImportError:  # окружения без pywin32
    pythoncom = None


def _init_com() -> None:
    """Инициализирует COM для текущего потока, если это возможно."""
    if pythoncom is None:
        return
    # Повторный вызов в уже инициализированном апартаменте — не ошибка.
    with contextlib.suppress(Exception):
        pythoncom.CoInitialize()


def _get_wmi_connection() -> Any | None:
    """Создаёт соединение с WMI. Возвращает None, если это не удалось."""
    _init_com()
    try:
        # find_classes=False заметно ускоряет инициализацию.
        return wmi.WMI(find_classes=False)
    except Exception as exc:
        print(f"WMI connection failed in worker process: {exc}")
        return None


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

    Используется метод `associators` самой библиотеки: он корректно строит
    путь к объекту. Ручной WQL требовал экранирования `\\\\.\\PHYSICALDRIVE0`
    и возвращал WBEM_E_NOT_FOUND. Важно и имя аргумента — `wmi_result_class`;
    с прежним `wmi_class` вызов падал на TypeError, и разделы никогда не
    попадали в отчёт.
    """
    partitions: list[dict[str, Any]] = []

    try:
        associated = disk.associators(wmi_result_class="Win32_DiskPartition")
    except Exception as exc:
        print(f"Disk partition lookup failed: {exc}")
        return partitions

    for part in associated:
        try:
            logical_disks = part.associators(wmi_result_class="Win32_LogicalDisk")
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


def get_services_worker() -> dict[str, Any]:
    """
    Точка входа воркера: службы, доступные для оптимизации.

    Системные службы из System32 и хосты svchost исключаются: их изменение
    рискованно, а объём данных для модели заметно вырастает.
    """
    wmi_con = _get_wmi_connection()
    if wmi_con is None:
        return {"error": "WMI connection failed."}

    try:
        rows = wmi_con.query(
            "SELECT Name, DisplayName, State, StartMode, PathName "
            "FROM Win32_Service WHERE StartMode != 'Disabled'"
        )
    except Exception as exc:
        return {"error": str(exc)}

    services: list[dict[str, Any]] = []
    for service in rows:
        path = service.PathName
        if path and ("system32" in path.lower() or "svchost" in path.lower()):
            continue
        services.append(
            {
                "name": service.Name,
                "display_name": service.DisplayName,
                "state": service.State,
                "start_mode": service.StartMode,
                "path": path,
            }
        )
    return {"services": services}


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
