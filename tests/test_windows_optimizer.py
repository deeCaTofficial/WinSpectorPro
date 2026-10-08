# tests/test_windows_optimizer.py
"""
Тесты применения плана. Ни один тест не выполняет настоящих команд —
подменяются `_run_process` и `apply_service_action`, поэтому система остаётся
нетронутой.
"""

from __future__ import annotations

import subprocess
from typing import Any

import pytest

from winspector.core.exceptions import RestorePointError
from winspector.core.modules import windows_optimizer as optimizer_module
from winspector.core.modules.service_control import ServiceControlError
from winspector.core.modules.windows_optimizer import ServiceOperation, WindowsOptimizer


def completed(returncode: int = 0, stdout: str = "", stderr: str = "") -> Any:
    return subprocess.CompletedProcess(
        args=["powershell.exe"], returncode=returncode, stdout=stdout, stderr=stderr
    )


@pytest.fixture
def optimizer() -> WindowsOptimizer:
    opt = WindowsOptimizer(optimization_rules=[])
    # Считаем, что все упомянутые в тестах службы существуют.
    opt._service_cache = {
        "mapsbroker",
        "diagtrack",
        "steamhelper",
        "rpcss",
        "windefend",
        "legacycritical",
    }
    return opt


# --- Генерация команд ------------------------------------------------------


class TestCommandGeneration:
    @pytest.mark.parametrize("action", ["disable", "set_manual", "stop"])
    def test_service_actions_become_api_operations(self, optimizer, action):
        """
        Служба не превращается в командную строку: имя уходит в API SCM
        аргументом, поэтому и подставлять его некуда.
        """
        command = optimizer._generate_command_for_action(
            {"type": "service", "id": "MapsBroker", "action": action}
        )
        assert command == ServiceOperation("MapsBroker", action)

    def test_unknown_service_action_is_skipped(self, optimizer):
        command = optimizer._generate_command_for_action(
            {"type": "service", "id": "MapsBroker", "action": "restart"}
        )
        assert command is None

    def test_uwp_uses_package_full_name_when_present(self, optimizer):
        command = optimizer._generate_command_for_action(
            {
                "type": "uwp_app",
                "id": "Microsoft.BingWeather",
                "action": "remove",
                "package_full_name": "Microsoft.BingWeather_4.0_x64__8wekyb3d8bbwe",
            }
        )
        assert "-PackageFullName 'Microsoft.BingWeather_4.0_x64__8wekyb3d8bbwe'" in command[-1]

    def test_uwp_falls_back_to_name(self, optimizer):
        command = optimizer._generate_command_for_action(
            {"type": "uwp_app", "id": "Microsoft.BingWeather", "action": "remove"}
        )
        assert "-Name 'Microsoft.BingWeather'" in command[-1]

    @pytest.mark.parametrize(
        "malicious_id",
        [
            "Maps'; Remove-Item C:\\ -Recurse -Force; '",
            "Maps'; Stop-Computer; '",
            "svc | Out-File C:\\evil.txt",
            "svc`nStop-Computer",
            "svc$(Get-Content C:\\secrets.txt)",
        ],
    )
    def test_injection_never_produces_a_command(self, optimizer, malicious_id):
        """
        Второй барьер: даже в обход валидатора генератор обязан отказать.
        """
        command = optimizer._generate_command_for_action(
            {"type": "service", "id": malicious_id, "action": "disable"}
        )
        assert command is None

    def test_critical_service_refused_at_command_level(self, optimizer):
        command = optimizer._generate_command_for_action(
            {"type": "service", "id": "RpcSs", "action": "disable"}
        )
        assert command is None

    def test_critical_uwp_refused_at_command_level(self, optimizer):
        command = optimizer._generate_command_for_action(
            {"type": "uwp_app", "id": "Microsoft.WindowsStore", "action": "remove"}
        )
        assert command is None

    def test_absent_service_is_skipped(self, optimizer):
        command = optimizer._generate_command_for_action(
            {"type": "service", "id": "NoSuchService", "action": "disable"}
        )
        assert command is None

    @pytest.mark.parametrize(
        "item",
        [
            {"type": "service", "id": "MapsBroker"},
            {"type": "service", "action": "disable"},
            {"id": "MapsBroker", "action": "disable"},
            {"type": "service", "id": "MapsBroker", "action": "explode"},
            {"type": "driver", "id": "MapsBroker", "action": "disable"},
        ],
    )
    def test_incomplete_or_unknown_items_return_none(self, optimizer, item):
        assert optimizer._generate_command_for_action(item) is None


# --- Выполнение плана ------------------------------------------------------


class TestExecuteActionPlan:
    async def test_empty_plan_short_circuits(self, optimizer):
        summary = await optimizer.execute_action_plan([])
        assert summary == {"completed": [], "failed": [], "skipped": []}

    async def test_results_match_their_own_items(self, optimizer, monkeypatch):
        """
        Регрессия: раньше результаты сопоставлялись с планом по индексу, и
        любой пропущенный пункт сдвигал нумерацию — в отчёт попадали чужие
        записи. Первый пункт здесь пропускается (службы нет в системе).
        """
        plan = [
            {"type": "service", "id": "GhostService", "action": "disable"},  # пропуск
            {"type": "service", "id": "MapsBroker", "action": "disable"},  # успех
            {"type": "service", "id": "DiagTrack", "action": "disable"},  # ошибка
        ]

        def fake_apply(name, action):
            if name == "DiagTrack":
                raise ServiceControlError("Отказано в доступе")

        monkeypatch.setattr(optimizer, "_cache_existing_services", _noop)
        monkeypatch.setattr(optimizer_module, "apply_service_action", fake_apply)

        summary = await optimizer.execute_action_plan(plan)

        assert [i["id"] for i in summary["skipped"]] == ["GhostService"]
        assert [i["id"] for i in summary["completed"]] == ["MapsBroker"]
        assert [e["item"]["id"] for e in summary["failed"]] == ["DiagTrack"]

    async def test_timeout_is_reported_as_failure(self, optimizer, monkeypatch):
        async def fake_run(command, timeout):
            return None  # так `_run_process` сообщает о таймауте

        monkeypatch.setattr(optimizer, "_cache_existing_services", _noop)
        monkeypatch.setattr(optimizer, "_run_process", fake_run)

        summary = await optimizer.execute_action_plan(
            [{"type": "uwp_app", "id": "Vendor.App", "action": "remove"}]
        )

        assert len(summary["failed"]) == 1
        assert "Таймаут" in summary["failed"][0]["error"]

    async def test_service_failure_message_reaches_report(self, optimizer, monkeypatch):
        def fake_apply(name, action):
            raise ServiceControlError("Служба 'MapsBroker' не остановилась за отведённое время.")

        monkeypatch.setattr(optimizer, "_cache_existing_services", _noop)
        monkeypatch.setattr(optimizer_module, "apply_service_action", fake_apply)

        summary = await optimizer.execute_action_plan(
            [{"type": "service", "id": "MapsBroker", "action": "stop"}]
        )

        assert summary["completed"] == []
        assert "не остановилась" in summary["failed"][0]["error"]

    async def test_services_and_uwp_execute_through_their_own_paths(self, optimizer, monkeypatch):
        applied: list[tuple[str, str]] = []
        commands: list[list[str]] = []

        def fake_apply(name, action):
            applied.append((name, action))

        async def fake_run(command, timeout):
            commands.append(command)
            return completed()

        monkeypatch.setattr(optimizer, "_cache_existing_services", _noop)
        monkeypatch.setattr(optimizer_module, "apply_service_action", fake_apply)
        monkeypatch.setattr(optimizer, "_run_process", fake_run)

        summary = await optimizer.execute_action_plan(
            [
                {"type": "service", "id": "MapsBroker", "action": "set_manual"},
                {"type": "uwp_app", "id": "Vendor.App", "action": "remove"},
            ]
        )

        assert applied == [("MapsBroker", "set_manual")]
        assert len(commands) == 1 and commands[0][0] == "powershell.exe"
        assert [i["id"] for i in summary["completed"]] == ["MapsBroker", "Vendor.App"]

    async def test_progress_callback_stays_in_range(self, optimizer, monkeypatch):
        seen: list[int] = []

        monkeypatch.setattr(optimizer, "_cache_existing_services", _noop)
        monkeypatch.setattr(optimizer_module, "apply_service_action", lambda name, action: None)

        await optimizer.execute_action_plan(
            [
                {"type": "service", "id": "MapsBroker", "action": "disable"},
                {"type": "service", "id": "DiagTrack", "action": "disable"},
            ],
            progress_callback=lambda value, text: seen.append(value),
        )

        assert seen, "Прогресс должен сообщаться"
        assert all(70 <= value <= 100 for value in seen)

    async def test_plan_of_only_skipped_items(self, optimizer, monkeypatch):
        monkeypatch.setattr(optimizer, "_cache_existing_services", _noop)
        summary = await optimizer.execute_action_plan(
            [{"type": "service", "id": "GhostService", "action": "disable"}]
        )
        assert len(summary["skipped"]) == 1
        assert summary["completed"] == []


# --- Сбор компонентов ------------------------------------------------------


class TestCollectComponents:
    async def test_uwp_parsing_filters_critical(self, optimizer, monkeypatch):
        payload = (
            '[{"Name":"Microsoft.BingWeather","PackageFullName":"Microsoft.BingWeather_4.0"},'
            '{"Name":"Microsoft.WindowsStore","PackageFullName":"Microsoft.WindowsStore_12"}]'
        )

        async def fake_run(command, timeout):
            return completed(stdout=payload)

        monkeypatch.setattr(optimizer, "_run_process", fake_run)

        apps = await optimizer._collect_uwp_apps()

        assert [a["id"] for a in apps] == ["Microsoft.BingWeather"]

    async def test_single_object_response_is_wrapped(self, optimizer, monkeypatch):
        async def fake_run(command, timeout):
            return completed(stdout='{"Name":"Solo.App","PackageFullName":"Solo.App_1"}')

        monkeypatch.setattr(optimizer, "_run_process", fake_run)
        assert len(await optimizer._collect_uwp_apps()) == 1

    async def test_broken_json_returns_empty(self, optimizer, monkeypatch):
        async def fake_run(command, timeout):
            return completed(stdout="not json at all")

        monkeypatch.setattr(optimizer, "_run_process", fake_run)
        assert await optimizer._collect_uwp_apps() == []

    async def test_command_failure_returns_empty(self, optimizer, monkeypatch):
        async def fake_run(command, timeout):
            return completed(returncode=1, stderr="access denied")

        monkeypatch.setattr(optimizer, "_run_process", fake_run)
        assert await optimizer._collect_uwp_apps() == []

    async def test_service_collector_failure_does_not_break_collection(
        self, optimizer, monkeypatch
    ):
        """Сбой опроса SCM не должен ронять весь сбор данных."""

        async def exploding_services():
            raise RuntimeError("SCM недоступен")

        async def fake_run(command, timeout):
            return completed(stdout="[]")

        monkeypatch.setattr(optimizer, "_collect_services", exploding_services)
        monkeypatch.setattr(optimizer, "_run_process", fake_run)

        result = await optimizer.get_system_components()

        assert result == {"services": [], "uwp_apps": []}

    async def test_services_and_apps_are_collected_together(self, optimizer, monkeypatch):
        async def fake_services():
            return [{"name": "Third", "display_name": "Сторонняя"}]

        async def fake_run(command, timeout):
            return completed(stdout='[{"Name":"Solo.App","PackageFullName":"Solo.App_1"}]')

        monkeypatch.setattr(optimizer, "_collect_services", fake_services)
        monkeypatch.setattr(optimizer, "_run_process", fake_run)

        result = await optimizer.get_system_components()

        assert [s["name"] for s in result["services"]] == ["Third"]
        assert [a["id"] for a in result["uwp_apps"]] == ["Solo.App"]


# --- Сбор служб через SCM ---------------------------------------------------


class FakeWinService:
    """Дублёр `psutil.WindowsService` с управляемым `as_dict()`."""

    def __init__(self, info: dict[str, Any] | None, error: Exception | None = None) -> None:
        self._info = info or {}
        self._error = error

    def as_dict(self) -> dict[str, Any]:
        if self._error:
            raise self._error
        return dict(self._info)

    def name(self) -> str:
        return self._info["name"]


def fake_service(
    name: str, path: str, start_type: str = "automatic", status: str = "running"
) -> FakeWinService:
    return FakeWinService(
        {
            "name": name,
            "display_name": f"{name} display",
            "binpath": path,
            "start_type": start_type,
            "status": status,
        }
    )


class TestCollectServices:
    """`collect_services` фильтрует то же, что раньше фильтровал WMI-воркер."""

    def test_system_services_are_filtered_out(self, monkeypatch):
        """
        Службы из System32, хосты svchost и отключённые службы не предлагаются
        к изменению — это снижает риск и уменьшает объём данных для модели.
        """
        rows = [
            fake_service("Third", r"C:\Apps\third.exe"),
            fake_service("Sys", r"C:\Windows\System32\svc.exe"),
            fake_service("Host", r"C:\Windows\svchost.exe -k net"),
            fake_service("Off", r"C:\Apps\off.exe", start_type="disabled"),
        ]
        monkeypatch.setattr(optimizer_module.psutil, "win_service_iter", lambda: iter(rows))

        assert [s["name"] for s in optimizer_module.collect_services()] == ["Third"]

    def test_entries_keep_wmi_vocabulary(self, monkeypatch):
        """
        Форма и словарь записей совпадают с прежним WMI-воркером: база знаний
        и промпты ИИ опираются на `Auto`/`Manual` и `Running`/`Stopped`.
        """
        rows = [fake_service("App", r"C:\Apps\a.exe", "manual", "stop_pending")]
        monkeypatch.setattr(optimizer_module.psutil, "win_service_iter", lambda: iter(rows))

        (entry,) = optimizer_module.collect_services()

        assert set(entry) == {"name", "display_name", "state", "start_mode", "path"}
        assert entry["start_mode"] == "Manual"
        assert entry["state"] == "Stop Pending"

    def test_inaccessible_service_is_skipped_not_raised(self, monkeypatch):
        rows = [
            FakeWinService(None, error=PermissionError("нет доступа")),
            fake_service("Ok", r"C:\Apps\ok.exe"),
        ]
        monkeypatch.setattr(optimizer_module.psutil, "win_service_iter", lambda: iter(rows))

        assert [s["name"] for s in optimizer_module.collect_services()] == ["Ok"]

    def test_service_without_path_is_kept(self, monkeypatch):
        rows = [fake_service("NoPath", "", "manual", "stopped")]
        monkeypatch.setattr(optimizer_module.psutil, "win_service_iter", lambda: iter(rows))

        (entry,) = optimizer_module.collect_services()
        assert entry["path"] is None

    async def test_service_cache_uses_scm_enumeration(self, optimizer, monkeypatch):
        rows = [fake_service("MapsBroker", r"C:\x.exe"), fake_service("Other", r"C:\y.exe")]
        monkeypatch.setattr(optimizer_module.psutil, "win_service_iter", lambda: iter(rows))

        await optimizer._cache_existing_services()

        assert optimizer._service_cache == {"mapsbroker", "other"}


# --- Точка восстановления --------------------------------------------------


class TestRestorePoint:
    async def test_success_is_silent(self, optimizer, monkeypatch):
        async def fake_run(command, timeout):
            return completed(returncode=0)

        monkeypatch.setattr(optimizer, "_run_process", fake_run)
        await optimizer.create_restore_point()  # не должно бросать

    async def test_failure_raises_actionable_error(self, optimizer, monkeypatch):
        async def fake_run(command, timeout):
            return completed(returncode=1, stderr="System protection is disabled")

        monkeypatch.setattr(optimizer, "_run_process", fake_run)

        with pytest.raises(RestorePointError) as exc:
            await optimizer.create_restore_point()

        assert "защита системы" in str(exc.value).lower()

    async def test_recent_point_is_not_an_error(self, optimizer, monkeypatch):
        """
        Windows разрешает одну точку в 1440 минут. Если свежая точка уже есть,
        откат пользователю доступен — прерывать работу не нужно.
        """

        async def fake_run(command, timeout):
            return completed(
                returncode=1,
                stderr="A restore point was created recently (1440 minutes)",
            )

        monkeypatch.setattr(optimizer, "_run_process", fake_run)
        await optimizer.create_restore_point()  # не должно бросать

    async def test_timeout_raises(self, optimizer, monkeypatch):
        async def fake_run(command, timeout):
            return None

        monkeypatch.setattr(optimizer, "_run_process", fake_run)
        with pytest.raises(RestorePointError):
            await optimizer.create_restore_point()


async def _noop(*args: Any, **kwargs: Any) -> None:
    """Заглушка для методов, обращающихся к системе."""
    return None
