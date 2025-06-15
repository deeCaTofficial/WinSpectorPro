# src/winspector/core/modules/user_profiler.py
"""
Модуль для сбора неперсональных данных о системе с целью
построения детального профиля использования компьютера.
"""

import os
import wmi
import psutil
import asyncio
import logging
import winreg
from typing import Dict, Any, List, Set, Optional
from pathlib import Path

from concurrent.futures import ProcessPoolExecutor
from ..wmi_workers import get_hardware_info_worker

# Импортируем базовый класс для работы с WMI
from .wmi_base import WMIBase

logger = logging.getLogger(__name__)


class UserProfiler(WMIBase):
    """
    Анализирует систему для определения профиля пользователя (геймер, разработчик и т.д.).
    Наследуется от WMIBase для безопасной работы с WMI в потоках.
    """

    def __init__(self, profiler_config: Dict[str, Any]):
        """
        Инициализирует профилировщик.

        Args:
            profiler_config: Секция 'user_profiler_config' из knowledge_base.yaml.
        """
        super().__init__() # Инициализируем базовый класс WMI
        logger.info("Инициализация UserProfiler (Advanced)...")
        self.config = profiler_config

    async def get_system_profile(self) -> Dict[str, Any]:
        """
        Собирает многогранный, детальный профиль использования ПК.
        Запускает все задачи сбора данных параллельно.
        """
        logger.info("Начало сбора данных для профилирования системы.")
        
        loop = asyncio.get_running_loop()
        with ProcessPoolExecutor() as pool:
            # Запускаем worker-функцию в отдельном процессе
            hardware_task = loop.run_in_executor(pool, get_hardware_info_worker)
            
            # Задачи, не использующие WMI, можно запускать в потоках
            software_task = asyncio.to_thread(self._get_installed_software_from_registry)
            markers_task = asyncio.to_thread(self._scan_for_profile_markers)

            hardware, software, markers = await asyncio.gather(
                hardware_task, software_task, markers_task, return_exceptions=True
            )
        
        # Обрабатываем возможные ошибки
        if isinstance(hardware, Exception):
            logger.error(f"Ошибка при сборе данных об оборудовании: {hardware}")
            hardware = {}
        if isinstance(software, Exception):
            logger.error(f"Ошибка при сборе данных о ПО: {software}")
            software = []
        if isinstance(markers, Exception):
            logger.error(f"Ошибка при поиске файловых маркеров: {markers}")
            markers = {}

        profile = {
            "hardware": hardware,
            "installed_software": software,
            "file_system_markers": markers
        }
        
        logger.info("Профилирование системы завершено.")
        logger.debug(f"Собранный профиль: {profile}")
        return profile

    def _get_hardware_info(self) -> Dict[str, Any]:
        """Собирает информацию об аппаратном обеспечении через WMI."""
        logger.debug("Сбор информации об оборудовании через WMI...")
        hardware: Dict[str, Any] = {"gpu": []}
        try:
            # Используем wmi_instance из базового класса WMIBase
            wmi_con = self.wmi_instance
            
            # CPU
            cpu_info = wmi_con.Win32_Processor()
            if cpu_info:
                hardware['cpu'] = cpu_info[0].Name.strip()

            # GPU
            gpu_info = wmi_con.Win32_VideoController()
            if gpu_info:
                # Берем только активные видеоадаптеры, исключая виртуальные
                hardware['gpu'] = [
                    gpu.Name.strip() for gpu in gpu_info if gpu.AdapterCompatibility is not None
                ]

            # RAM
            mem_info = wmi_con.Win32_ComputerSystem()
            if mem_info:
                ram_bytes = int(mem_info[0].TotalPhysicalMemory)
                hardware['ram_gb'] = round(ram_bytes / (1024**3))
                
        except wmi.x_wmi as e:
            logger.error(f"Ошибка при сборе данных об оборудовании через WMI: {e}")
            return {} # Возвращаем пустой словарь в случае ошибки WMI
        
        return hardware

    def _get_installed_software_from_registry(self) -> List[str]:
        """Собирает список установленного ПО из реестра Windows."""
        logger.debug("Сбор списка установленного ПО из реестра...")
        installed_software: Set[str] = set()
        
        # Пути в реестре, где хранится информация об установленных программах
        uninstall_paths = [
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
            r"SOFTWARE\Wow6432Node\Microsoft\Windows\CurrentVersion\Uninstall"
        ]
        
        for path in uninstall_paths:
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path) as key:
                    for i in range(winreg.QueryInfoKey(key)[0]):
                        try:
                            subkey_name = winreg.EnumKey(key, i)
                            with winreg.OpenKey(key, subkey_name) as subkey:
                                # Пытаемся прочитать флаги. Если их нет, считаем, что это обычное приложение.
                                try:
                                    is_system_component = winreg.QueryValueEx(subkey, "SystemComponent")[0]
                                    if is_system_component == 1:
                                        continue
                                except FileNotFoundError:
                                    pass  # Ключа нет, это нормально

                                try:
                                    release_type = winreg.QueryValueEx(subkey, "ReleaseType")[0]
                                    if "Update" in release_type:
                                        continue
                                except FileNotFoundError:
                                    pass  # Ключа нет, это нормально

                                # Если мы дошли сюда, это обычное приложение. Читаем его имя.
                                display_name = winreg.QueryValueEx(subkey, "DisplayName")[0]
                                if display_name:
                                    installed_software.add(display_name.strip())

                        except (OSError, FileNotFoundError):
                            continue  # Пропускаем ключи, которые не удалось прочитать
            except FileNotFoundError:
                continue # Пропускаем, если ветка реестра не существует (например, на 32-битной системе)
        
        logger.debug(f"Найдено {len(installed_software)} записей о ПО в реестре.")
        return sorted(list(installed_software))

    def _scan_for_profile_markers(self) -> Dict[str, List[str]]:
        """Ищет файловые маркеры (ключевые папки) для подтверждения профиля."""
        logger.debug("Поиск файловых маркеров профиля...")
        markers_found: Dict[str, List[str]] = {
            profile: [] for profile in self.config.get("filesystem_markers", {})
        }
        
        user_profile_path = Path.home()
        
        # Сканируем все доступные диски для поиска библиотек (Steam и др.)
        fixed_drives = [Path(p.mountpoint) for p in psutil.disk_partitions() if 'fixed' in p.opts]
        
        for profile, markers in self.config.get("filesystem_markers", {}).items():
            for marker in markers:
                # Пути могут быть абсолютными (для дисков) или относительными (от дома)
                # Убираем ведущий слэш, чтобы .join работал корректно
                marker_path = Path(marker.lstrip("\\/"))
                
                # Ищем в домашней папке
                if (user_profile_path / marker_path).exists():
                    markers_found[profile].append(str(user_profile_path / marker_path))
                    logger.debug(f"Найден маркер '{marker}' для профиля '{profile}'.")
                    continue # Переходим к следующему маркеру
                
                # Ищем на всех дисках (для игровых библиотек)
                for drive in fixed_drives:
                    if (drive / marker_path).exists():
                        markers_found[profile].append(str(drive / marker_path))
                        logger.debug(f"Найден маркер '{marker}' для профиля '{profile}' на диске {drive}.")
                        break # Нашли на одном диске, ищем следующий маркер
        
        # Убираем профили, для которых ничего не найдено
        return {k: v for k, v in markers_found.items() if v}