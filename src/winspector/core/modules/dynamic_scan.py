# src/winspector/core/modules/dynamic_scan.py
"""
Модуль для "живого" анализа системы, включая сбор детальных данных
о запущенных процессах для ИИ-анализа и выявления аномалий.
"""

import psutil
import asyncio
import os
import logging
from dataclasses import dataclass, field
from typing import List, Set, Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProcessInfo:
    """
    Структура данных для хранения информации о запущенном процессе.
    `frozen=True` делает экземпляры неизменяемыми, что повышает надежность.
    """
    pid: int
    name: str
    cpu_percent: float
    memory_mb: float
    path: Optional[str] = None
    command_line: Optional[str] = None
    parent_name: Optional[str] = None


class DynamicAnalyzer:
    """
    Выполняет динамический анализ системы, фокусируясь на запущенных процессах.
    """
    
    # Список имен критических системных процессов, которые следует игнорировать.
    # Используем множество (set) для быстрой проверки `in`.
    CRITICAL_PROCESSES: Set[str] = {
        'system idle process', 'system', 'registry', 'smss.exe', 
        'csrss.exe', 'wininit.exe', 'services.exe', 'lsass.exe',
        'winlogon.exe', 'fontdrvhost.exe', 'dwm.exe', 'explorer.exe',
        'svchost.exe'
    }

    def __init__(self, telemetry_domains_path: Optional[str] = None):
        """
        Инициализирует анализатор.

        Args:
            telemetry_domains_path: Опциональный путь к файлу с черным списком доменов.
        """
        logger.info("Инициализация DynamicAnalyzer (Advanced)...")
        self.telemetry_blacklist: Set[str] = set()
        if telemetry_domains_path:
            self.telemetry_blacklist = self._load_blacklist(telemetry_domains_path)

    def _load_blacklist(self, path: str) -> Set[str]:
        """Загружает черный список доменов из файла."""
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return {line.strip().lower() for line in f if line.strip() and not line.startswith('#')}
        except FileNotFoundError:
            logger.warning(f"Файл черного списка не найден: {path}")
            return set()

    async def get_resource_intensive_processes(
        self, cpu_threshold: float = 0.5, mem_threshold_mb: float = 50.0
    ) -> List[ProcessInfo]:
        """
        Асинхронно собирает информацию о процессах, превышающих заданные пороги
        потребления CPU или памяти.

        Args:
            cpu_threshold: Порог загрузки CPU в процентах.
            mem_threshold_mb: Порог потребления памяти в мегабайтах.

        Returns:
            Список объектов ProcessInfo для ресурсоемких процессов.
        """
        logger.debug(f"Поиск процессов с CPU > {cpu_threshold}% или RAM > {mem_threshold_mb}MB...")
        
        # Выполняем синхронную, блокирующую операцию в отдельном потоке
        running_processes = await asyncio.to_thread(
            self._collect_all_processes, cpu_threshold, mem_threshold_mb
        )
        
        logger.info(f"Найдено {len(running_processes)} ресурсоемких процессов.")
        return running_processes

    def _collect_all_processes(
        self, cpu_threshold: float, mem_threshold_mb: float
    ) -> List[ProcessInfo]:
        """
        Синхронная функция для итерации по всем процессам и их фильтрации.
        Эта функция предназначена для запуска через `asyncio.to_thread`.
        """
        collected_data: List[ProcessInfo] = []
        
        # Запрашиваем только те атрибуты, которые нам нужны, для повышения производительности
        attrs = ['pid', 'name', 'exe', 'cmdline', 'ppid', 'cpu_percent', 'memory_info']
        
        for proc in psutil.process_iter(attrs=attrs, ad_value=None):
            try:
                proc_info = proc.info
                
                # Пропускаем критические и "пустые" процессы
                if not proc_info['exe'] or proc_info['name'].lower() in self.CRITICAL_PROCESSES:
                    continue
                
                # Фильтруем по потреблению ресурсов
                cpu_usage = proc_info.get('cpu_percent', 0.0)
                mem_usage_mb = proc_info['memory_info'].rss / (1024 * 1024)
                
                if cpu_usage < cpu_threshold and mem_usage_mb < mem_threshold_mb:
                    continue

                # Получаем имя родительского процесса
                parent_name = None
                if proc_info.get('ppid'):
                    try:
                        parent = psutil.Process(proc_info['ppid'])
                        parent_name = parent.name()
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass # Это нормально для некоторых системных процессов

                # Собираем все в dataclass
                process_data = ProcessInfo(
                    pid=proc_info['pid'],
                    name=proc_info['name'],
                    path=proc_info['exe'],
                    command_line=" ".join(proc_info['cmdline']) if proc_info.get('cmdline') else None,
                    parent_name=parent_name,
                    cpu_percent=round(cpu_usage, 2),
                    memory_mb=round(mem_usage_mb, 2)
                )
                collected_data.append(process_data)
                
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                # Эти ошибки ожидаемы при сканировании, просто пропускаем процесс
                continue
            
        return collected_data

    # В будущем здесь можно добавить другие методы динамического анализа, например:
    # async def analyze_network_connections(self) -> List[NetworkFinding]:
    #     ...