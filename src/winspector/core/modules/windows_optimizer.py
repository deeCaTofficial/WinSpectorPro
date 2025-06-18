# src/winspector/core/modules/windows_optimizer.py
"""
Модуль для оптимизации Windows: собирает данные о службах и UWP-приложениях,
а затем выполняет детальный план по их изменению.
"""
import asyncio
import json
import logging
import subprocess
import shlex
from typing import List, Dict, Any, Callable, Optional, Set
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime
import os
from pathlib import Path

# ### FIX: Import the necessary worker function ###
from ..wmi_workers import get_services_worker

logger = logging.getLogger(__name__)


class WindowsOptimizer:
    """
    Модуль для выполнения низкоуровневых оптимизаций Windows.
    """
    def __init__(self, optimization_rules: List[Dict]):
        logger.info("Инициализация WindowsOptimizer (Advanced)...")
        self.rules = optimization_rules
        self._service_cache: Optional[Set[str]] = None

    async def get_system_components(self) -> Dict[str, List[Dict]]:
        """Собирает компоненты, делегируя вызовы воркерам."""
        logger.info("Начало сбора данных о компонентах системы (службы, UWP).")
        
        loop = asyncio.get_running_loop()
        with ProcessPoolExecutor(max_workers=1) as pool:
            services_task = loop.run_in_executor(pool, get_services_worker)
        
        apps_task = self._collect_uwp_apps()
        
        services_result, apps_result = await asyncio.gather(services_task, apps_task, return_exceptions=True)

        services = []
        if isinstance(services_result, dict) and 'services' in services_result:
            services = services_result['services']
        elif isinstance(services_result, Exception):
            logger.error(f"Ошибка при сборе служб: {services_result}", exc_info=services_result)

        apps = []
        if isinstance(apps_result, list):
            apps = apps_result
        elif isinstance(apps_result, Exception):
            logger.error(f"Ошибка при сборе UWP-приложений: {apps_result}", exc_info=apps_result)
            
        logger.info(f"Сбор завершен. Найдено служб: {len(services)}, UWP-приложений: {len(apps)}.")
        return {"services": services, "uwp_apps": apps}

    async def _collect_uwp_apps(self) -> List[Dict]:
        """Собирает список установленных UWP-приложений через PowerShell."""
        command = (
            'powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "'
            'Get-AppxPackage -AllUsers | '
            'Where-Object {$_.IsFramework -eq $false -and $_.NonRemovable -eq $false} | '
            'Select-Object Name, PackageFullName, IsFramework | '
            'ConvertTo-Json -Compress"'
        )
        result = await asyncio.to_thread(
            lambda: subprocess.run(command, capture_output=True, text=True, shell=True, check=False)
        )
        if result.returncode != 0 or not result.stdout:
            logger.error(f"Ошибка при сборе UWP-приложений: {result.stderr}")
            return []
        try:
            apps_data = json.loads(result.stdout)
            if not isinstance(apps_data, list):
                apps_data = [apps_data]
            return [{"id": app.get("Name"), "package_full_name": app.get("PackageFullName")} for app in apps_data]
        except json.JSONDecodeError:
            logger.error("Не удалось распарсить JSON-ответ от PowerShell при сборе UWP.")
            return []

    async def execute_action_plan(self, plan: List[Dict], progress_callback: Callable[[int, str], None]) -> Dict[str, List[Any]]:
        """
        Параллельно выполняет все действия из плана, собирая детальный отчет.
        """
        logger.info(f"Начало выполнения плана деблоатинга из {len(plan)} действий.")
        summary = {"completed": [], "failed": []}
        
        if not plan:
            progress_callback(85, "Оптимизация системных компонентов не требуется.")
            return summary

        await self._cache_existing_services()

        tasks = []
        for item in plan:
            command = self._generate_command_for_action(item)
            if command:
                tasks.append(self._run_single_command(item, command))
        
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for i, res in enumerate(results):
            if res is None:
                continue
            item = plan[i]
            progress_callback(70 + int(15 * ((i + 1) / len(plan))), f"Завершено: {item.get('user_explanation_ru', item['id'])}")
            if isinstance(res, Exception):
                logger.error(f"Критическая ошибка при выполнении команды для '{item['id']}': {res}", exc_info=res)
                summary["failed"].append({"item": item, "error": str(res)})
            else:
                summary[res["status"]].append(res["data"])

        progress_callback(85, "Оптимизация системных компонентов завершена.")
        return summary
    
    async def _cache_existing_services(self):
        """Получает и кэширует имена всех служб в системе."""
        logger.debug("Кэширование списка существующих служб...")
        command = 'powershell.exe -Command "Get-Service | Select-Object -ExpandProperty Name"'
        result = await asyncio.to_thread(
            lambda: subprocess.run(command, capture_output=True, text=True, shell=True, check=False)
        )
        if result.returncode == 0:
            self._service_cache = {name.lower() for name in result.stdout.splitlines()}
        else:
            logger.error("Не удалось получить список служб для кэширования.")
            self._service_cache = set()

    def _generate_command_for_action(self, item: Dict) -> Optional[List[str]]:
        """Генерирует одну PowerShell команду в виде безопасного списка аргументов."""
        item_id = item.get("id")
        action = item.get("action")
        target_type = item.get("type")

        if not all([item_id, action, target_type]):
            return None

        if target_type == "service" and self._service_cache is not None:
            if item_id.lower() not in self._service_cache:
                logger.info(f"Пропуск действия для отсутствующей службы: '{item_id}'")
                return None

        script_block = ""
        if target_type == "service":
            if action == "disable":
                script_block = f"Stop-Service -Name '{item_id}' -Force -ErrorAction SilentlyContinue; Set-Service -Name '{item_id}' -StartupType Disabled"
            elif action == "set_manual":
                script_block = f"Set-Service -Name '{item_id}' -StartupType Manual"
            elif action == "stop":
                script_block = f"Stop-Service -Name '{item_id}' -Force"
        
        elif target_type == "uwp_app" and action == "remove":
            pfn = item.get("package_full_name")
            if pfn:
                safe_pfn = pfn.replace("'", "''")
                script_block = f"Get-AppxPackage -AllUsers -PackageFullName '{safe_pfn}' | Remove-AppxPackage -AllUsers"
            else:
                safe_item_id = item_id.replace("'", "''")
                script_block = f"Get-AppxPackage -AllUsers -Name '*{safe_item_id}*' | Remove-AppxPackage -AllUsers"
        
        if script_block:
            return ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script_block]
        return None

    async def _run_single_command(self, item: Dict, command: List[str]) -> Dict[str, Any]:
        """Асинхронно выполняет одну команду и возвращает результат."""
        result = await asyncio.to_thread(
            lambda: subprocess.run(command, capture_output=True, text=True, shell=False, check=False, encoding='utf-8', errors='ignore')
        )
        if result.returncode == 0:
            logger.info(f"Успешно выполнено действие '{item['action']}' для '{item['id']}'.")
            return {"status": "completed", "data": item}
        else:
            error_msg = result.stderr.strip() or "Неизвестная ошибка PowerShell"
            logger.error(f"Ошибка при выполнении действия для '{item['id']}': {error_msg}")
            return {"status": "failed", "data": {"item": item, "error": error_msg}}

    def create_restore_point(self) -> None:
        """
        Создает точку восстановления системы через PowerShell, принудительно
        включая необходимые службы, если они отключены.
        """
        system_drive = os.environ.get("SystemDrive", "C:")
        description = f"WinSpector Pro Backup - {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        
        logger.info("Формирование и запуск команды PowerShell для создания точки восстановления.")
        
        script_block = f"""
        $services = @("vss", "swprv")
        $servicesToRestart = @{{}}
        foreach ($serviceName in $services) {{
            $service = Get-Service -Name $serviceName -ErrorAction SilentlyContinue
            if ($service -and $service.Status -ne "Running") {{
                $servicesToRestart[$serviceName] = $service.StartType
                try {{
                    Set-Service -Name $serviceName -StartupType Automatic -ErrorAction Stop
                    Start-Service -Name $serviceName -ErrorAction Stop
                    Write-Host "Service '$serviceName' started."
                }} catch {{
                    Write-Warning "Failed to start service '$serviceName': $_"
                }}
            }}
        }}
        Checkpoint-Computer -Description '{description}' -RestorePointType 'MODIFY_SETTINGS'
        foreach ($name in $servicesToRestart.Keys) {{
            try {{
                Set-Service -Name $name -StartupType $servicesToRestart[$name] -ErrorAction Stop
                Write-Host "Service '$name' startup type restored to '$($servicesToRestart[$name])'."
            }} catch {{
                Write-Warning "Failed to restore startup type for service '$name': $_"
            }}
        }}
        """
        
        command = f"powershell -ExecutionPolicy Bypass -NoProfile -Command \"{script_block}\""

        try:
            result = subprocess.run(
                command, shell=True, capture_output=True, text=True, check=False,
                encoding='utf-8', errors='ignore'
            )
            if result.returncode == 0:
                logger.info("Команда создания точки восстановления успешно выполнена.")
            else:
                error_output = result.stderr.strip()
                if "не удалось создать" in error_output or "could not be created" in error_output:
                    logger.error(f"Не удалось создать точку восстановления. Ошибка PowerShell: {error_output}")
                    raise RuntimeError(f"Не удалось создать точку восстановления: {error_output}")
                else:
                    logger.warning(f"Команда создания точки восстановления завершилась с кодом {result.returncode}, но, возможно, успешно. Вывод: {error_output or result.stdout.strip()}")
        except FileNotFoundError:
            logger.error("Не удалось найти PowerShell. Убедитесь, что он установлен и доступен в PATH.")
            raise
        except Exception as e:
            logger.error(f"Произошла ошибка при создании точки восстановления: {e}", exc_info=True)
            raise