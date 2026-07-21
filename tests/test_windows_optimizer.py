# tests/test_windows_optimizer.py
"""
Тесты применения плана. Ни один тест не выполняет настоящих команд —
подменяется `_run_process`, поэтому система остаётся нетронутой.
"""

from __future__ import annotations

import subprocess
from typing import Any

import pytest

from winspector.core.exceptions import RestorePointError
from winspector.core.modules.windows_optimizer import WindowsOptimizer


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
    def test_disable_service_builds_argument_list(self, optimizer):
        command = optimizer._generate_command_for_action(
            {"type": "service", "id": "MapsBroker", "action": "disable"}
        )
        assert command[0] == "powershell.exe"
        assert "-Command" in command
        # Аргументы передаются списком: интерпретатор командной строки
        # не участвует, склейки строк нет.
        assert isinstance(command, list)
        assert "Set-Service -Name 'MapsBroker' -StartupType Disabled" in command[-1]

    def test_set_manual_is_supported(self, optimizer):
        command = optimizer._generate_command_for_action(
            {"type": "service", "id": "MapsBroker", "action": "set_manual"}
        )
        assert "StartupType Manual" in command[-1]

    def test_stop_is_supported(self, optimizer):
        command = optimizer._generate_command_for_action(
            {"type": "service", "id": "MapsBroker", "action": "stop"}
        )
        assert "Stop-Service" in command[-1]

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

        async def fake_run(command, timeout):
            script = command[-1]
            if "DiagTrack" in script:
                return completed(returncode=1, stderr="Отказано в доступе")
            return completed(returncode=0)

        monkeypatch.setattr(optimizer, "_cache_existing_services", _noop)
        monkeypatch.setattr(optimizer, "_run_process", fake_run)

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
            [{"type": "service", "id": "MapsBroker", "action": "disable"}]
        )

        assert len(summary["failed"]) == 1
        assert "Таймаут" in summary["failed"][0]["error"]

    async def test_progress_callback_stays_in_range(self, optimizer, monkeypatch):
        seen: list[int] = []

        async def fake_run(command, timeout):
            return completed()

        monkeypatch.setattr(optimizer, "_cache_existing_services", _noop)
        monkeypatch.setattr(optimizer, "_run_process", fake_run)

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

    async def test_worker_failure_does_not_break_collection(self, optimizer, monkeypatch):
        """Падение WMI-воркера не должно ронять весь сбор данных."""

        class ExplodingPool:
            async def run(self, func, *args):
                raise RuntimeError("WMI недоступен")

        async def fake_run(command, timeout):
            return completed(stdout="[]")

        optimizer._worker_pool = ExplodingPool()
        monkeypatch.setattr(optimizer, "_run_process", fake_run)

        result = await optimizer.get_system_components()

        assert result == {"services": [], "uwp_apps": []}

    async def test_worker_error_dict_is_handled(self, optimizer, monkeypatch):
        class ErrorPool:
            async def run(self, func, *args):
                return {"error": "WMI connection failed."}

        async def fake_run(command, timeout):
            return completed(stdout="[]")

        optimizer._worker_pool = ErrorPool()
        monkeypatch.setattr(optimizer, "_run_process", fake_run)

        assert (await optimizer.get_system_components())["services"] == []


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
