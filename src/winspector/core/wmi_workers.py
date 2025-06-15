# src/winspector/core/wmi_workers.py
import wmi
import psutil
import winreg
from typing import Dict, Any, List, Set

# Этот файл будет запускаться в отдельном процессе.
# Он не должен импортировать ничего из Qt или других частей вашего приложения.

def get_hardware_info_worker() -> Dict[str, Any]:
    """Worker-функция для сбора информации об оборудовании."""
    try:
        wmi_con = wmi.WMI()
        hardware = {"gpu": []}
        cpu_info = wmi_con.Win32_Processor()
        if cpu_info:
            hardware['cpu'] = cpu_info[0].Name.strip()
        gpu_info = wmi_con.Win32_VideoController()
        if gpu_info:
            hardware['gpu'] = [gpu.Name.strip() for gpu in gpu_info if gpu.AdapterCompatibility is not None]
        mem_info = wmi_con.Win32_ComputerSystem()
        if mem_info:
            ram_bytes = int(mem_info[0].TotalPhysicalMemory)
            hardware['ram_gb'] = round(ram_bytes / (1024**3))
        return hardware
    except Exception as e:
        # Возвращаем ошибку, чтобы главный процесс знал о ней
        return {"error": str(e)}

def get_services_worker() -> Dict[str, Any]:
    """Worker-функция для сбора информации о службах."""
    try:
        wmi_con = wmi.WMI()
        services = []
        for s in wmi_con.Win32_Service():
            if s.PathName and ("microsoft" not in s.PathName.lower() or "windows" not in s.PathName.lower()):
                services.append({
                    "name": s.Name, "display_name": s.DisplayName,
                    "state": s.State, "start_mode": s.StartMode, "path": s.PathName
                })
        return {"services": services}
    except Exception as e:
        return {"error": str(e)}

# Другие функции, не использующие WMI, можно оставить в их классах или тоже перенести сюда