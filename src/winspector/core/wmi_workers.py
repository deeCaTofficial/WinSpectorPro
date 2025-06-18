# src/winspector/core/wmi_workers.py
"""
Worker-функции, выполняемые в отдельных процессах для безопасного
и изолированного сбора системной информации через WMI.

Эта версия включает оптимизированные WQL-запросы и асинхронное выполнение
для максимальной производительности и надежности.
"""
import asyncio
import wmi
from typing import Any, Dict, List, Optional


def _get_wmi_connection() -> Optional[Any]:
    """
    Вспомогательная функция для инкапсуляции создания WMI-соединения.
    В случае сбоя подключения к WMI возвращает None.
    """
    try:
        # find_classes=False может немного ускорить инициализацию
        return wmi.WMI(find_classes=False)
    except wmi.x_wmi as e:
        print(f"WMI connection failed in worker process: {e}")
        return None


async def _get_hardware_info_async(wmi_con: Any) -> Dict[str, Any]:
    """Асинхронно и параллельно собирает всю информацию об оборудовании."""
    hardware: Dict[str, Any] = {"gpu": [], "disks": []}

    # Запускаем все WMI-запросы параллельно в потоках, чтобы не блокировать event loop
    cpu_task = asyncio.to_thread(wmi_con.query, "SELECT Name, NumberOfCores, NumberOfLogicalProcessors FROM Win32_Processor")
    gpu_task = asyncio.to_thread(wmi_con.query, "SELECT Name, DriverVersion, AdapterRAM, AdapterCompatibility FROM Win32_VideoController")
    ram_task = asyncio.to_thread(wmi_con.query, "SELECT TotalPhysicalMemory FROM Win32_ComputerSystem")
    board_task = asyncio.to_thread(wmi_con.query, "SELECT Manufacturer, Product FROM Win32_BaseBoard")
    disk_task = asyncio.to_thread(wmi_con.query, "SELECT DeviceID, Model, Size, MediaType FROM Win32_DiskDrive")

    # Ожидаем завершения всех асинхронных задач
    results = await asyncio.gather(cpu_task, gpu_task, ram_task, board_task, disk_task, return_exceptions=True)
    cpu_res, gpu_res, ram_res, board_res, disk_res = results

    # Обрабатываем результаты каждой задачи, проверяя на ошибки
    if not isinstance(cpu_res, Exception) and cpu_res:
        cpu_info = cpu_res[0]
        hardware['cpu'] = {"name": cpu_info.Name.strip(), "cores": cpu_info.NumberOfCores, "threads": cpu_info.NumberOfLogicalProcessors}
    
    if not isinstance(gpu_res, Exception) and gpu_res:
        for gpu in gpu_res:
            if gpu.AdapterCompatibility and "Microsoft" not in gpu.AdapterCompatibility:
                hardware['gpu'].append({"name": gpu.Name.strip(), "driver_version": gpu.DriverVersion, "vram_mb": round(int(gpu.AdapterRAM) / (1024**2)) if gpu.AdapterRAM else None})

    if not isinstance(ram_res, Exception) and ram_res:
        hardware['ram_gb'] = round(int(ram_res[0].TotalPhysicalMemory) / (1024**3))

    if not isinstance(board_res, Exception) and board_res:
        hardware['motherboard'] = {"manufacturer": board_res[0].Manufacturer.strip(), "product": board_res[0].Product.strip()}

    if not isinstance(disk_res, Exception) and disk_res:
        for disk in disk_res:
            disk_data = {"model": disk.Model.strip(), "size_gb": round(int(disk.Size) / (1024**3)) if disk.Size else None, "media_type": disk.MediaType, "partitions": []}
            try:
                # Связываем физический диск с разделами, а разделы с логическими дисками
                partitions = wmi_con.query(f"ASSOCIATORS OF {{Win32_DiskDrive.DeviceID='{disk.DeviceID}'}} WHERE AssocClass=Win32_DiskDriveToDiskPartition")
                for part in partitions:
                    logical_disks = part.associators(wmi_class="Win32_LogicalDisk")
                    for logical in logical_disks:
                        disk_data["partitions"].append({
                            "drive_letter": logical.DeviceID,
                            "volume_name": logical.VolumeName,
                            "file_system": logical.FileSystem,
                            "free_space_gb": round(int(logical.FreeSpace) / (1024**3)) if logical.FreeSpace else None,
                        })
            except Exception:
                # Если не удалось получить разделы, просто пропускаем эту информацию
                pass
            hardware['disks'].append(disk_data)

    return hardware


def get_hardware_info_worker() -> Dict[str, Any]:
    """Синхронная обертка для асинхронного сбора данных об оборудовании."""
    wmi_con = _get_wmi_connection()
    if not wmi_con:
        return {"error": "WMI connection failed."}
    try:
        # Запускаем и выполняем асинхронную функцию
        return asyncio.run(_get_hardware_info_async(wmi_con))
    except Exception as e:
        return {"error": f"Async hardware collection failed: {e}"}


def get_services_worker() -> Dict[str, Any]:
    """
    Worker-функция для сбора информации о службах, которые НЕ отключены.
    Использует оптимизированный WQL-запрос для повышения производительности.
    """
    wmi_con = _get_wmi_connection()
    if not wmi_con:
        return {"error": "WMI connection failed."}

    services: List[Dict[str, Any]] = []
    try:
        # Запрос выбирает только нужные поля и только у не отключенных служб
        wmi_query = "SELECT Name, DisplayName, State, StartMode, PathName FROM Win32_Service WHERE StartMode != 'Disabled'"
        for s in wmi_con.query(wmi_query):
            path = s.PathName
            # Дополнительно фильтруем системные службы Microsoft для уменьшения "шума"
            if path and ("system32" in path.lower() or "svchost" in path.lower()):
                continue

            services.append({
                "name": s.Name,
                "display_name": s.DisplayName,
                "state": s.State,
                "start_mode": s.StartMode,
                "path": path,
            })
        return {"services": services}
    except Exception as e:
        return {"error": str(e)}


def get_running_processes_worker() -> Dict[str, Any]:
    """
    Worker-функция для сбора информации о запущенных процессах,
    исключая системные и доверенные процессы.
    """
    wmi_con = _get_wmi_connection()
    if not wmi_con:
        return {"error": "WMI connection failed."}

    processes: List[Dict[str, Any]] = []
    system_accounts = {'local system', 'system', 'network service'}

    try:
        # Оптимизированный запрос, выбираем только нужные поля
        wmi_query = "SELECT ProcessId, Name, ExecutablePath, CommandLine FROM Win32_Process"
        for p in wmi_con.query(wmi_query):
            try:
                owner_info = p.GetOwner()
                owner_domain = (owner_info[2] or "").lower()
                owner_user = (owner_info[0] or "").lower()

                # Пропускаем процессы, принадлежащие системным учетным записям
                if owner_domain in system_accounts or owner_user in system_accounts:
                    continue

                processes.append({
                    "pid": p.ProcessId,
                    "name": p.Name,
                    "path": p.ExecutablePath,
                    "command_line": p.CommandLine,
                    "owner": f"{owner_domain}\\{owner_user}"
                })
            except Exception:
                # Пропускаем процессы, к которым нет доступа (например, защищенные)
                continue
        return {"processes": processes}
    except Exception as e:
        return {"error": str(e)}