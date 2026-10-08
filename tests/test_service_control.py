# tests/test_service_control.py
"""
Тесты управления службами через API SCM.

`win32service` целиком подменяется дублёром: ни один тест не открывает
настоящий диспетчер служб и не меняет состояние системы.
"""

from __future__ import annotations

from typing import Any

import pytest
import pywintypes
import win32service
import winerror

from winspector.core.modules import service_control
from winspector.core.modules.service_control import ServiceControlError, apply_service_action

RUNNING = win32service.SERVICE_RUNNING
STOPPED = win32service.SERVICE_STOPPED


def win32_error(code: int, func: str = "OpenService") -> pywintypes.error:
    return pywintypes.error(code, func, f"win32 error {code}")


class FakeSCM:
    """
    Дублёр диспетчера служб.

    Хранит службы как словари `{state, start_type, dependents, stoppable}` и
    журналирует вызовы API, чтобы тесты проверяли не только результат, но и
    порядок операций.
    """

    def __init__(self, services: dict[str, dict[str, Any]]) -> None:
        self.services = services
        self.calls: list[tuple[str, Any]] = []
        self.open_handles = 0

    # --- поверхность win32service, которую использует модуль ---

    def OpenSCManager(self, machine, database, access):
        self.calls.append(("OpenSCManager", access))
        self.open_handles += 1
        return "scm"

    def OpenService(self, scm, name, access):
        self.calls.append(("OpenService", name))
        if name not in self.services:
            raise win32_error(winerror.ERROR_SERVICE_DOES_NOT_EXIST)
        if self.services[name].get("access_denied"):
            raise win32_error(winerror.ERROR_ACCESS_DENIED)
        self.open_handles += 1
        return name

    def CloseServiceHandle(self, handle):
        self.open_handles -= 1

    def EnumDependentServices(self, handle, state):
        return [
            (dep, f"{dep} display", ())
            for dep in self.services[handle].get("dependents", [])
            if self.services[dep]["state"] == RUNNING
        ]

    def ControlService(self, handle, control):
        self.calls.append(("ControlService", handle))
        service = self.services[handle]
        if service["state"] == STOPPED:
            raise win32_error(winerror.ERROR_SERVICE_NOT_ACTIVE, "ControlService")
        if not service.get("stoppable", True):
            raise win32_error(winerror.ERROR_INVALID_SERVICE_CONTROL, "ControlService")
        service["state"] = STOPPED
        return ()

    def QueryServiceStatus(self, handle):
        return (0, self.services[handle]["state"], 0, 0, 0, 0, 0)

    def ChangeServiceConfig(self, handle, service_type, start_type, *rest):
        self.calls.append(("ChangeServiceConfig", handle, start_type))
        assert service_type == win32service.SERVICE_NO_CHANGE
        self.services[handle]["start_type"] = start_type

    def __getattr__(self, name: str) -> Any:
        # Константы (SERVICE_STOP, SERVICE_DISABLED, ...) берём из настоящего модуля.
        return getattr(win32service, name)


@pytest.fixture
def scm(monkeypatch) -> FakeSCM:
    fake = FakeSCM(
        {
            "MapsBroker": {"state": RUNNING, "start_type": win32service.SERVICE_AUTO_START},
            "Stopped": {"state": STOPPED, "start_type": win32service.SERVICE_AUTO_START},
            "Parent": {
                "state": RUNNING,
                "start_type": win32service.SERVICE_AUTO_START,
                "dependents": ["Child", "IdleChild"],
            },
            "Child": {"state": RUNNING, "start_type": win32service.SERVICE_DEMAND_START},
            "IdleChild": {"state": STOPPED, "start_type": win32service.SERVICE_DEMAND_START},
            "Stubborn": {
                "state": RUNNING,
                "start_type": win32service.SERVICE_AUTO_START,
                "stoppable": False,
            },
            "Locked": {"state": RUNNING, "start_type": 2, "access_denied": True},
        }
    )
    monkeypatch.setattr(service_control, "win32service", fake)
    # Ожидание остановки не должно замедлять тесты.
    monkeypatch.setattr(service_control.time, "sleep", lambda _: None)
    return fake


class TestSetStartType:
    def test_set_manual_changes_only_start_type(self, scm):
        apply_service_action("MapsBroker", "set_manual")

        assert scm.services["MapsBroker"]["start_type"] == win32service.SERVICE_DEMAND_START
        # Служба не останавливалась: перевод в ручной запуск обратим и мягок.
        assert scm.services["MapsBroker"]["state"] == RUNNING
        assert ("ControlService", "MapsBroker") not in scm.calls

    def test_disable_stops_then_disables(self, scm):
        apply_service_action("MapsBroker", "disable")

        assert scm.services["MapsBroker"]["state"] == STOPPED
        assert scm.services["MapsBroker"]["start_type"] == win32service.SERVICE_DISABLED
        order = [c[0] for c in scm.calls if c[0] in ("ControlService", "ChangeServiceConfig")]
        assert order == ["ControlService", "ChangeServiceConfig"]

    def test_disable_proceeds_when_stop_is_refused(self, scm):
        """
        Как `Stop-Service -ErrorAction SilentlyContinue; Set-Service Disabled`:
        служба, не принимающая STOP, всё равно не поднимется после перезагрузки.
        """
        apply_service_action("Stubborn", "disable")

        assert scm.services["Stubborn"]["state"] == RUNNING
        assert scm.services["Stubborn"]["start_type"] == win32service.SERVICE_DISABLED


class TestStop:
    def test_stop_already_stopped_service_is_not_an_error(self, scm):
        apply_service_action("Stopped", "stop")
        assert scm.services["Stopped"]["state"] == STOPPED

    def test_stop_halts_active_dependents_first(self, scm):
        apply_service_action("Parent", "stop")

        stopped = [c[1] for c in scm.calls if c[0] == "ControlService"]
        assert stopped == ["Child", "Parent"], "зависимые — раньше родителя"
        assert scm.services["Child"]["state"] == STOPPED
        # Уже остановленная зависимая служба не трогается.
        assert ("OpenService", "IdleChild") not in scm.calls

    def test_stop_refused_by_service_is_reported(self, scm):
        with pytest.raises(ServiceControlError, match="Не удалось остановить"):
            apply_service_action("Stubborn", "stop")

    def test_stop_timeout_is_reported(self, scm, monkeypatch):
        # Служба «принимает» STOP, но состояние не меняется.
        def never_stops(handle, control):
            return ()

        scm.ControlService = never_stops
        clock = iter([0.0, 0.0, 100.0, 100.0])
        monkeypatch.setattr(service_control.time, "monotonic", lambda: next(clock))

        with pytest.raises(ServiceControlError, match="не остановилась"):
            apply_service_action("MapsBroker", "stop", stop_timeout=1.0)


class TestErrors:
    def test_missing_service_has_clear_message(self, scm):
        with pytest.raises(ServiceControlError, match="не найдена"):
            apply_service_action("NoSuchService", "set_manual")

    def test_access_denied_includes_code(self, scm):
        with pytest.raises(ServiceControlError, match="код 5"):
            apply_service_action("Locked", "set_manual")

    def test_unknown_action_is_rejected_before_touching_scm(self, scm):
        with pytest.raises(ServiceControlError, match="Неизвестное действие"):
            apply_service_action("MapsBroker", "restart")
        assert scm.calls == []

    @pytest.mark.parametrize("action", ["stop", "set_manual", "disable"])
    def test_all_handles_are_closed(self, scm, action):
        apply_service_action("Parent", action)
        assert scm.open_handles == 0

    def test_handles_are_closed_on_failure(self, scm):
        with pytest.raises(ServiceControlError):
            apply_service_action("Stubborn", "stop")
        assert scm.open_handles == 0
