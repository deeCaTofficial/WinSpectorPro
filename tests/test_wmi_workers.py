# tests/test_wmi_workers.py
"""
Тесты WMI-воркера оборудования.

Воркер выполняется в отдельном процессе, поэтому он обязан возвращать
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
