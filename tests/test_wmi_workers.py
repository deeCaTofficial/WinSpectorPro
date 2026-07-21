# tests/test_wmi_workers.py
"""
Тесты WMI-воркеров.

Воркеры выполняются в отдельном процессе, поэтому они обязаны возвращать
ошибку значением, а не исключением: необработанное исключение в подпроцессе
превращается в невнятный сбой пула.
"""

from __future__ import annotations

import pytest

from winspector.core import wmi_workers


class FakeWMI:
    """Дублёр соединения WMI с управляемым поведением запросов."""

    def __init__(self, rows=None, error: Exception | None = None) -> None:
        self.rows = rows or []
        self.error = error

    def query(self, wql: str):
        if self.error:
            raise self.error
        return self.rows


class FakeService:
    def __init__(self, name, display_name, state, start_mode, path) -> None:
        self.Name = name
        self.DisplayName = display_name
        self.State = state
        self.StartMode = start_mode
        self.PathName = path


class TestServicesWorker:
    def test_returns_error_value_when_wmi_unavailable(self, monkeypatch):
        monkeypatch.setattr(wmi_workers, "_get_wmi_connection", lambda: None)
        result = wmi_workers.get_services_worker()
        assert result == {"error": "WMI connection failed."}

    def test_query_failure_is_returned_not_raised(self, monkeypatch):
        monkeypatch.setattr(
            wmi_workers,
            "_get_wmi_connection",
            lambda: FakeWMI(error=RuntimeError("WQL сломался")),
        )
        result = wmi_workers.get_services_worker()
        assert "error" in result

    def test_system_services_are_filtered_out(self, monkeypatch):
        """
        Службы из System32 и svchost не предлагаются к изменению — это
        снижает риск и уменьшает объём данных для модели.
        """
        rows = [
            FakeService("Third", "Сторонняя", "Running", "Auto", r"C:\Apps\third.exe"),
            FakeService("Sys", "Системная", "Running", "Auto", r"C:\Windows\System32\svc.exe"),
            FakeService("Host", "Хост", "Running", "Auto", r"C:\Windows\svchost.exe -k net"),
        ]
        monkeypatch.setattr(wmi_workers, "_get_wmi_connection", lambda: FakeWMI(rows=rows))

        result = wmi_workers.get_services_worker()

        assert [s["name"] for s in result["services"]] == ["Third"]

    def test_service_entries_have_expected_shape(self, monkeypatch):
        rows = [FakeService("App", "Приложение", "Running", "Auto", r"C:\Apps\a.exe")]
        monkeypatch.setattr(wmi_workers, "_get_wmi_connection", lambda: FakeWMI(rows=rows))

        entry = wmi_workers.get_services_worker()["services"][0]

        assert set(entry) == {"name", "display_name", "state", "start_mode", "path"}

    def test_service_without_path_is_kept(self, monkeypatch):
        rows = [FakeService("NoPath", "Без пути", "Stopped", "Manual", None)]
        monkeypatch.setattr(wmi_workers, "_get_wmi_connection", lambda: FakeWMI(rows=rows))
        assert len(wmi_workers.get_services_worker()["services"]) == 1


class TestHardwareWorker:
    def test_returns_error_value_when_wmi_unavailable(self, monkeypatch):
        monkeypatch.setattr(wmi_workers, "_get_wmi_connection", lambda: None)
        assert wmi_workers.get_hardware_info_worker() == {"error": "WMI connection failed."}

    def test_internal_failure_is_wrapped(self, monkeypatch):
        monkeypatch.setattr(
            wmi_workers, "_get_wmi_connection", lambda: FakeWMI(error=RuntimeError("сбой"))
        )
        result = wmi_workers.get_hardware_info_worker()
        assert isinstance(result, dict)
        assert "gpu" in result or "error" in result


@pytest.mark.windows
class TestAgainstRealWmi:
    """Проверка на настоящем WMI."""

    def test_services_worker_survives_real_system(self):
        result = wmi_workers.get_services_worker()
        assert isinstance(result, dict)
        assert "services" in result or "error" in result

    def test_hardware_worker_returns_actual_data(self):
        """
        Регрессия: запросы выполнялись через `asyncio.to_thread`, из-за чего
        COM отвечал CO_E_NOTINITIALIZED и раздел `hardware` приходил пустым.
        Проверяем именно наличие данных, а не отсутствие исключения.
        """
        result = wmi_workers.get_hardware_info_worker()

        assert "error" not in result, result.get("error")
        assert result.get("cpu", {}).get("name"), "процессор не определён"
        assert result.get("ram_gb", 0) > 0, "объём памяти не определён"

    def test_disk_partitions_are_resolved(self):
        """
        Регрессия: `associators(wmi_class=...)` — несуществующий аргумент
        (правильный `wmi_result_class`), поэтому разделы никогда не собирались.
        """
        result = wmi_workers.get_hardware_info_worker()
        disks = result.get("disks") or []
        if not disks:
            pytest.skip("Физические диски не обнаружены через WMI")

        assert any(disk.get("partitions") for disk in disks), (
            "ни у одного диска не определены разделы"
        )

    def test_result_is_json_serialisable(self):
        """Результат уходит в промпт через json.dumps."""
        import json

        assert json.dumps(wmi_workers.get_hardware_info_worker(), default=str)
