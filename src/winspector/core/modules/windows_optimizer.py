# src/winspector/core/modules/windows_optimizer.py
"""
Сбор сведений о компонентах Windows и применение плана оптимизации.

Все внешние команды запускаются списком аргументов при `shell=False`, а любой
идентификатор перед подстановкой проверяется по строгому шаблону
(`plan_validator.is_safe_identifier`). Даже если ответ модели окажется
враждебным, он не сможет выйти за пределы имени службы.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
from collections.abc import Callable
from datetime import datetime
from typing import Any

from ..exceptions import RestorePointError
from ..wmi_workers import get_services_worker
from ..worker_pool import WorkerPool
from .plan_validator import is_critical_service, is_critical_uwp, is_safe_identifier

logger = logging.getLogger(__name__)

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_PS_BASE = [
    "powershell.exe",
    "-NoProfile",
    "-NonInteractive",
    "-ExecutionPolicy",
    "Bypass",
    "-Command",
]

DEFAULT_COMMAND_TIMEOUT = 120
RESTORE_POINT_TIMEOUT = 300


def _ps_quote(value: str) -> str:
    """Экранирует строку для одинарных кавычек PowerShell."""
    return value.replace("'", "''")


class WindowsOptimizer:
    """Читает состав системы и выполняет одобренные действия."""

    def __init__(
        self,
        optimization_rules: list[dict[str, Any]] | None = None,
        worker_pool: WorkerPool | None = None,
    ) -> None:
        logger.info("Инициализация WindowsOptimizer...")
        self.rules = optimization_rules or []
        self._worker_pool = worker_pool or WorkerPool()
        self._service_cache: set[str] | None = None

    # --- Сбор данных ------------------------------------------------------

    async def get_system_components(self) -> dict[str, list[dict[str, Any]]]:
        """Возвращает службы и UWP-приложения, пригодные для оптимизации."""
        logger.info("Сбор данных о компонентах системы.")

        services_result, apps_result = await asyncio.gather(
            self._worker_pool.run(get_services_worker),
            self._collect_uwp_apps(),
            return_exceptions=True,
        )

        services: list[dict[str, Any]] = []
        if isinstance(services_result, BaseException):
            logger.error("Не удалось собрать службы: %s", services_result)
        elif isinstance(services_result, dict):
            if error := services_result.get("error"):
                logger.error("Воркер WMI вернул ошибку: %s", error)
            services = services_result.get("services") or []

        apps: list[dict[str, Any]] = []
        if isinstance(apps_result, BaseException):
            logger.error("Не удалось собрать UWP-приложения: %s", apps_result)
        elif isinstance(apps_result, list):
            apps = apps_result

        logger.info("Найдено служб: %d, UWP-приложений: %d.", len(services), len(apps))
        return {"services": services, "uwp_apps": apps}

    async def _collect_uwp_apps(self) -> list[dict[str, Any]]:
        """Собирает список устанавливаемых UWP-пакетов через PowerShell."""
        script = (
            "Get-AppxPackage | "
            "Where-Object { -not $_.IsFramework -and -not $_.NonRemovable } | "
            "Select-Object Name, PackageFullName | "
            "ConvertTo-Json -Compress -Depth 3"
        )
        result = await self._run_powershell(script, timeout=DEFAULT_COMMAND_TIMEOUT)
        if result is None or result.returncode != 0 or not result.stdout.strip():
            logger.error(
                "Не удалось получить список UWP-приложений: %s",
                (result.stderr.strip() if result else "команда не выполнена"),
            )
            return []

        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            logger.error("Некорректный JSON от PowerShell при сборе UWP.")
            return []

        if isinstance(data, dict):
            data = [data]

        apps: list[dict[str, Any]] = []
        for entry in data:
            if not isinstance(entry, dict):
                continue
            name = entry.get("Name")
            if not name or is_critical_uwp(str(name)):
                continue
            apps.append({"id": name, "package_full_name": entry.get("PackageFullName")})
        return apps

    async def _cache_existing_services(self) -> None:
        """Кеширует имена существующих служб, чтобы не выполнять лишние команды."""
        result = await self._run_powershell(
            "Get-Service | Select-Object -ExpandProperty Name",
            timeout=DEFAULT_COMMAND_TIMEOUT,
        )
        if result is None or result.returncode != 0:
            logger.error("Не удалось получить список служб для кеширования.")
            self._service_cache = set()
            return
        self._service_cache = {
            line.strip().lower() for line in result.stdout.splitlines() if line.strip()
        }
        logger.debug("Закешировано служб: %d.", len(self._service_cache))

    # --- Выполнение плана -------------------------------------------------

    async def execute_action_plan(
        self,
        plan: list[dict[str, Any]],
        progress_callback: Callable[[int, str], None] | None = None,
    ) -> dict[str, list[Any]]:
        """
        Выполняет действия из плана и возвращает отчёт.

        Результаты сопоставляются с исходными пунктами явной парой
        (пункт, команда), а не по индексу: пропущенные пункты иначе сдвигают
        нумерацию и в отчёт попадают чужие записи.
        """
        summary: dict[str, list[Any]] = {"completed": [], "failed": [], "skipped": []}
        if not plan:
            if progress_callback:
                progress_callback(85, "Оптимизация компонентов не требуется.")
            return summary

        logger.info("Выполнение плана из %d действий.", len(plan))
        await self._cache_existing_services()

        runnable: list[tuple[dict[str, Any], list[str]]] = []
        for item in plan:
            command = self._generate_command_for_action(item)
            if command is None:
                summary["skipped"].append(item)
                continue
            runnable.append((item, command))

        if not runnable:
            if progress_callback:
                progress_callback(85, "Подходящих действий не найдено.")
            return summary

        results = await asyncio.gather(
            *(self._run_single_command(item, cmd) for item, cmd in runnable),
            return_exceptions=True,
        )

        total = len(runnable)
        for index, (result, (item, _)) in enumerate(zip(results, runnable, strict=True), start=1):
            if progress_callback:
                label = item.get("user_explanation_ru") or item["id"]
                progress_callback(70 + int(15 * index / total), f"Завершено: {label}")

            if isinstance(result, BaseException):
                logger.error("Ошибка выполнения для '%s': %s", item["id"], result)
                summary["failed"].append({"item": item, "error": str(result)})
            elif isinstance(result, dict):
                summary[result["status"]].append(result["data"])

        if progress_callback:
            progress_callback(85, "Оптимизация компонентов завершена.")

        logger.info(
            "План выполнен: успешно %d, с ошибкой %d, пропущено %d.",
            len(summary["completed"]),
            len(summary["failed"]),
            len(summary["skipped"]),
        )
        return summary

    def _generate_command_for_action(self, item: dict[str, Any]) -> list[str] | None:
        """
        Строит команду PowerShell для одного действия.

        Возвращает None, если действие нужно пропустить: неизвестный тип,
        небезопасный идентификатор, критический компонент или отсутствующая
        в системе служба.
        """
        item_id = item.get("id")
        action = item.get("action")
        target_type = item.get("type")

        if not (item_id and action and target_type):
            return None

        # Барьер, дублирующий валидатор: модуль не доверяет вызывающей стороне.
        if not is_safe_identifier(item_id):
            logger.error("Отклонён небезопасный идентификатор: %r", item_id)
            return None

        if target_type == "service":
            if is_critical_service(item_id):
                logger.error("Отклонено действие над критической службой '%s'.", item_id)
                return None
            if self._service_cache is not None and item_id.lower() not in self._service_cache:
                logger.info("Служба '%s' отсутствует в системе — пропуск.", item_id)
                return None
            return self._service_command(item_id, action)

        if target_type == "uwp_app" and action == "remove":
            if is_critical_uwp(item_id):
                logger.error("Отклонено удаление системного пакета '%s'.", item_id)
                return None
            return self._uwp_command(item)

        logger.debug("Действие '%s' для типа '%s' не поддержано.", action, target_type)
        return None

    @staticmethod
    def _service_command(item_id: str, action: str) -> list[str] | None:
        name = _ps_quote(item_id)
        if action == "disable":
            script = (
                f"Stop-Service -Name '{name}' -Force -ErrorAction SilentlyContinue; "
                f"Set-Service -Name '{name}' -StartupType Disabled -ErrorAction Stop"
            )
        elif action == "set_manual":
            script = f"Set-Service -Name '{name}' -StartupType Manual -ErrorAction Stop"
        elif action == "stop":
            script = f"Stop-Service -Name '{name}' -Force -ErrorAction Stop"
        else:
            return None
        return [*_PS_BASE, script]

    @staticmethod
    def _uwp_command(item: dict[str, Any]) -> list[str] | None:
        package_full_name = item.get("package_full_name")
        if package_full_name and is_safe_identifier(package_full_name):
            selector = f"-PackageFullName '{_ps_quote(package_full_name)}'"
        else:
            selector = f"-Name '{_ps_quote(item['id'])}'"
        script = f"Get-AppxPackage {selector} | Remove-AppxPackage -ErrorAction Stop"
        return [*_PS_BASE, script]

    async def _run_powershell(
        self, script: str, timeout: int
    ) -> subprocess.CompletedProcess[str] | None:
        """Выполняет PowerShell-скрипт без окна консоли и с ограничением времени."""
        return await self._run_process([*_PS_BASE, script], timeout=timeout)

    @staticmethod
    async def _run_process(
        command: list[str], timeout: int
    ) -> subprocess.CompletedProcess[str] | None:
        def _run() -> subprocess.CompletedProcess[str] | None:
            try:
                return subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    shell=False,
                    check=False,
                    encoding="utf-8",
                    errors="replace",
                    timeout=timeout,
                    creationflags=_NO_WINDOW,
                )
            except subprocess.TimeoutExpired:
                logger.error("Команда превысила лимит %d с: %s", timeout, command[:2])
                return None
            except (OSError, subprocess.SubprocessError) as exc:
                logger.error("Не удалось выполнить команду %s: %s", command[:2], exc)
                return None

        return await asyncio.to_thread(_run)

    async def _run_single_command(self, item: dict[str, Any], command: list[str]) -> dict[str, Any]:
        """Выполняет одну команду и возвращает её результат."""
        result = await self._run_process(command, timeout=DEFAULT_COMMAND_TIMEOUT)

        if result is None:
            return {
                "status": "failed",
                "data": {"item": item, "error": "Таймаут или сбой запуска команды"},
            }
        if result.returncode == 0:
            logger.info("Выполнено '%s' для '%s'.", item["action"], item["id"])
            return {"status": "completed", "data": item}

        error = result.stderr.strip() or f"PowerShell завершился с кодом {result.returncode}"
        logger.error("Ошибка действия для '%s': %s", item["id"], error)
        return {"status": "failed", "data": {"item": item, "error": error}}

    # --- Точка восстановления --------------------------------------------

    async def create_restore_point(self) -> None:
        """
        Создаёт точку восстановления системы.

        Raises:
            RestorePointError: защита системы отключена или служба недоступна.
                Отсутствие точки — не повод продолжать: без неё пользователь
                не сможет откатить изменения.
        """
        description = f"WinSpector Pro - {datetime.now():%Y-%m-%d %H:%M}"
        drive = os.environ.get("SystemDrive", "C:") + "\\"
        script = f"""
$ErrorActionPreference = 'Stop'
try {{ Enable-ComputerRestore -Drive '{_ps_quote(drive)}' }} catch {{ }}
foreach ($name in @('vss','swprv')) {{
    try {{
        $svc = Get-Service -Name $name -ErrorAction Stop
        if ($svc.StartType -eq 'Disabled') {{ Set-Service -Name $name -StartupType Manual }}
        if ($svc.Status -ne 'Running') {{ Start-Service -Name $name }}
    }} catch {{ Write-Warning "service $name : $_" }}
}}
Checkpoint-Computer -Description '{_ps_quote(description)}' -RestorePointType 'MODIFY_SETTINGS'
""".strip()

        result = await self._run_powershell(script, timeout=RESTORE_POINT_TIMEOUT)

        if result is None:
            raise RestorePointError(
                "Создание точки восстановления не завершилось за отведённое время."
            )
        if result.returncode == 0:
            logger.info("Точка восстановления создана.")
            return

        error = (result.stderr or result.stdout or "").strip()

        # Windows по умолчанию разрешает одну точку в сутки. Если свежая точка
        # уже есть, откат пользователю доступен — это не повод прерывать работу.
        frequency_markers = (
            "1440",
            "already",
            "只能",
            "частота",
            "frequency",
            "a restore point was created recently",
        )
        if any(marker.lower() in error.lower() for marker in frequency_markers):
            logger.warning(
                "Свежая точка восстановления уже существует, создание пропущено: %s",
                error,
            )
            return

        logger.error("Не удалось создать точку восстановления: %s", error)
        raise RestorePointError(
            "Не удалось создать точку восстановления. Проверьте, включена ли "
            f"защита системы для диска {drive}. Подробности: {error}"
        )
