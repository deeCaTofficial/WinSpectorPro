# src/winspector/core/modules/windows_optimizer.py
"""
Модуль для оптимизации Windows: собирает данные о службах и UWP-приложениях,
а затем генерирует и выполняет PowerShell-скрипт для их "деблоатинга".
"""
import asyncio
import json
import logging
from typing import List, Dict, Any, Callable, Tuple
from pathlib import Path
import tempfile
import subprocess
from datetime import datetime, timedelta
import os

from concurrent.futures import ProcessPoolExecutor
from ..wmi_workers import get_services_worker

# Наследуемся от базового класса для безопасной работы с WMI
from .wmi_base import WMIBase

logger = logging.getLogger(__name__)


class WindowsOptimizer:
    """
    Модуль для выполнения низкоуровневых оптимизаций Windows.
    Отвечает за сбор данных о компонентах и выполнение плана деблоатинга.
    """
    def __init__(self):
        super().__init__()
        logger.info("Инициализация WindowsOptimizer (Advanced)...")

    async def get_system_components(self) -> Dict[str, List[Dict]]:
        """Собирает компоненты, запуская WMI в отдельном процессе."""
        logger.info("Начало сбора данных о компонентах системы (службы, UWP).")
        
        loop = asyncio.get_running_loop()
        with ProcessPoolExecutor() as pool:
            services_task = loop.run_in_executor(pool, get_services_worker)
            apps_task = self._collect_uwp_apps()
            
            services_result, apps = await asyncio.gather(services_task, apps_task)

        services = services_result.get("services", [])
        
        # Обрабатываем возможные ошибки
        if isinstance(services, Exception):
            logger.error(f"Ошибка при сборе служб: {services}", exc_info=services)
            services = []
        if isinstance(apps, Exception):
            logger.error(f"Ошибка при сборе UWP-приложений: {apps}", exc_info=apps)
            apps = []
            
        logger.info(f"Сбор завершен. Найдено служб: {len(services)}, UWP-приложений: {len(apps)}.")
        return {"services": services, "uwp_apps": apps}

    def _collect_services(self) -> List[Dict]:
        """Собирает список всех служб, кроме критически важных для Microsoft."""
        services = []
        try:
            # Используем wmi_instance из базового класса WMIBase
            for s in self.wmi_instance.Win32_Service():
                # Фильтруем службы, которые являются частью ОС, чтобы уменьшить "шум"
                # Сначала проверяем, что у службы вообще есть путь
                if s.PathName and ("microsoft" not in s.PathName.lower() or "windows" not in s.PathName.lower()):
                    services.append({
                        "name": s.Name,
                        "display_name": s.DisplayName,
                        "state": s.State,
                        "start_mode": s.StartMode,
                        "path": s.PathName,
                    })
        except Exception as e:
            logger.error(f"Не удалось получить список служб через WMI: {e}", exc_info=True)
            # В случае ошибки возвращаем пустой список, а не роняем приложение
        return services

    async def _collect_uwp_apps(self) -> List[Dict]:
        """Собирает список установленных UWP-приложений через PowerShell."""
        command = (
            'powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "'
            'Get-AppxPackage -AllUsers | '
            'Where-Object {$_.IsFramework -eq $false -and $_.NonRemovable -eq $false} | '
            'Select-Object Name, PackageFullName, IsFramework | '
            'ConvertTo-Json -Compress"'
        )
        
        # Запускаем блокирующий вызов в отдельном потоке
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None,  # Используем стандартный ThreadPoolExecutor
            lambda: subprocess.run(command, capture_output=True, text=True, shell=True, check=False)
        )

        if result.returncode != 0:
            logger.error(f"Ошибка при сборе UWP-приложений: {result.stderr}")
            return []
            
        if not result.stdout:
            return []
            
        try:
            apps_data = json.loads(result.stdout)
            # PowerShell может вернуть один объект или список
            if not isinstance(apps_data, list):
                apps_data = [apps_data]
            return [{"id": app.get("Name"), "package_full_name": app.get("PackageFullName")} for app in apps_data]
        except json.JSONDecodeError:
            logger.error("Не удалось распарсить JSON-ответ от PowerShell при сборе UWP.")
            return []

    async def execute_action_plan(
        self, plan: List[Dict], progress_callback: Callable[[int, str], None]
    ) -> Dict:
        """
        Генерирует и выполняет PowerShell-скрипт на основе плана от ИИ.

        Args:
            plan: Список действий, сгенерированный ИИ.
            progress_callback: Функция для обновления прогресса в GUI.

        Returns:
            Словарь с отчетом о выполненных действиях.
        """
        logger.info("Начало выполнения плана деблоатинга.")
        summary = {"disabled_services": [], "removed_apps": [], "errors": []}
        
        script_lines = self._generate_powershell_script(plan)

        if not script_lines:
            progress_callback(85, "Оптимизация системных компонентов не требуется.")
            return summary
        
        # Собираем итоговый скрипт
        script_content = '$ErrorActionPreference = "SilentlyContinue";\n' + "\n".join(script_lines)
        logger.debug(f"Сгенерирован PowerShell скрипт:\n{script_content}")
        
        # Выполняем скрипт
        return await self._run_powershell_script(script_content, summary, plan, progress_callback)

    def _generate_powershell_script(self, plan: List[Dict]) -> List[str]:
        """Генерирует строки для PowerShell-скрипта на основе плана."""
        script_lines = []
        for item in plan:
            item_id = item.get("id")
            action = item.get("action")
            target_type = item.get("type")

            if not all([item_id, action, target_type]):
                continue

            if action == "disable" and target_type == "service":
                script_lines.append(f"# Отключение службы: {item_id}")
                script_lines.append(f"Stop-Service -Name '{item_id}' -Force;")
                script_lines.append(f"Set-Service -Name '{item_id}' -StartupType Disabled;")
            
            elif action == "remove" and target_type == "uwp_app":
                pfn = item.get("package_full_name")
                # Используем полное имя пакета, если оно есть - это надежнее
                if pfn:
                    script_lines.append(f"# Удаление UWP-приложения: {item_id}")
                    script_lines.append(f"Get-AppxPackage -AllUsers -PackageFullName '{pfn}' | Remove-AppxPackage -AllUsers;")
                else: # Если нет, пробуем по имени
                    script_lines.append(f"Get-AppxPackage -AllUsers -Name '*{item_id}*' | Remove-AppxPackage -AllUsers;")
        
        return script_lines

    async def _run_powershell_script(
        self, script_content: str, summary: Dict, plan: List[Dict], progress_callback: Callable[[int, str], None]
    ) -> Dict:
        """Записывает скрипт во временный файл и выполняет его."""
        try:
            with tempfile.NamedTemporaryFile(
                mode='w', suffix='.ps1', delete=False, encoding='utf-8-sig'
            ) as f:
                f.write(script_content)
                script_path = Path(f.name)
            
            progress_callback(75, f"Выполнение скрипта оптимизации...")
            command = f'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "{script_path}"'
            
            # Запускаем блокирующий вызов в отдельном потоке
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(
                None,
                lambda: subprocess.run(command, capture_output=True, text=True, shell=True, check=False)
            )
            
            if result.returncode != 0:
                error_details = result.stderr.strip()
                summary['errors'].append(f"Ошибка выполнения скрипта: {error_details}")
                logger.error(f"Ошибка выполнения скрипта оптимизации: {error_details}")
            else:
                # Если все успешно, заполняем отчет
                for item in plan:
                    if item.get("action") == "disable" and item.get("type") == "service":
                        summary["disabled_services"].append(item["id"])
                    elif item.get("action") == "remove" and item.get("type") == "uwp_app":
                        summary["removed_apps"].append(item["id"])

        finally:
            # Гарантированно удаляем временный файл
            if 'script_path' in locals() and script_path.exists():
                script_path.unlink()
        
        progress_callback(85, "Оптимизация системных компонентов завершена.")
        return summary

    def create_restore_point(self) -> None:
        """
        Создает точку восстановления системы через PowerShell.
        Эта функция является блокирующей и должна вызываться в отдельном потоке.
        """
        system_drive = os.environ.get("SystemDrive", "C:")
        description = f"WinSpector Pro Backup - {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        
        logger.info("Формирование и запуск команды PowerShell для создания точки восстановления.")
        
        # Команда PowerShell для выполнения
        command = (
            f"powershell -ExecutionPolicy Bypass -NoProfile -Command "
            f"\"Enable-ComputerRestore -Drive '{system_drive}'; "
            f"Checkpoint-Computer -Description '{description}' -RestorePointType 'MODIFY_SETTINGS'\""
        )

        try:
            # shell=True необходим для прямого выполнения сложных командных строк.
            # capture_output=True, чтобы перехватить stdout/stderr.
            # text=True, чтобы получить вывод в виде строки.
            # Не указываем encoding, чтобы Python использовал системную кодировку по умолчанию.
            result = subprocess.run(
                command, 
                shell=True, 
                capture_output=True, 
                text=True, 
                check=False # Не выбрасывать исключение при ненулевом коде возврата
            )

            if result.returncode == 0:
                logger.info("Команда создания точки восстановления успешно выполнена.")
            else:
                # PowerShell может выводить ошибки в stderr, даже если операция частично удалась
                error_output = result.stderr.strip()
                if "A new system restore point could not be created" in error_output or "не удалось создать" in error_output.lower():
                     logger.error(f"Не удалось создать точку восстановления. Ошибка PowerShell: {error_output}")
                     raise RuntimeError(f"Не удалось создать точку восстановления: {error_output}")
                else:
                    logger.warning(f"Команда создания точки восстановления завершилась с кодом {result.returncode}, но, возможно, успешно. "
                                   f"Вывод: {error_output or result.stdout.strip()}")

        except FileNotFoundError:
            logger.error("Не удалось найти PowerShell. Убедитесь, что он установлен и доступен в PATH.")
            raise
        except Exception as e:
            logger.error(f"Произошла ошибка при создании точки восстановления: {e}", exc_info=True)
            raise

    async def clear_temp_files(self) -> int:
        """Асинхронно очищает временные файлы и папки."""