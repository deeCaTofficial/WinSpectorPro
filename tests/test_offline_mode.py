# tests/test_offline_mode.py
"""
Тесты работы без ИИ.

Главное требование: пользователь без ключа Gemini получает работающую
программу, а не сообщение об ошибке после того, как система уже изменена.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from winspector.core import offline_planner
from winspector.core.analyzer import WinSpectorCore
from winspector.core.exceptions import AIUnavailableError

SERVICES = {
    "services": [
        {"name": "MapsBroker"},
        {"name": "DiagTrack"},
        {"name": "RpcSs"},
        {"name": "WinDefend"},
        {"name": "SafeService"},
    ]
}


@pytest.fixture
def offline_kb() -> dict[str, Any]:
    """База знаний с реальными именами служб в `targets`."""
    return {
        "optimization_rules": [
            {
                "id": "Svc_Safe",
                "type": "service",
                "safety": "high",
                "targets": ["SafeService"],
                "description_ru": "Безопасная служба.",
            },
            {
                "id": "Svc_Medium",
                "type": "service",
                "safety": "medium",
                "targets": ["MapsBroker"],
                "description_ru": "Средний риск.",
            },
            {
                "id": "Svc_Critical",
                "type": "service",
                "safety": "high",
                "targets": ["RpcSs"],
                "description_ru": "Совпадает с критической службой.",
            },
            {
                "id": "Svc_Absent",
                "type": "service",
                "safety": "high",
                "targets": ["NoSuchService"],
                "description_ru": "Службы нет в системе.",
            },
        ],
        "cleanup_rules": [
            {"category_id": "temp", "safety": "high", "paths": ["%TEMP%"]},
            {"category_id": "pip", "safety": "low", "paths": ["%LOCALAPPDATA%/pip"]},
            {"category_id": "browser", "safety": "medium", "paths": ["%LOCALAPPDATA%/br"]},
        ],
    }


class TestOfflinePlanner:
    def test_selects_only_safe_and_present_services(self, offline_kb):
        plan = offline_planner.build_action_plan(offline_kb, SERVICES)
        assert [action["id"] for action in plan] == ["safeservice"]

    def test_uses_reversible_action(self, offline_kb):
        """Без ИИ выбирается «вручную», а не отключение."""
        plan = offline_planner.build_action_plan(offline_kb, SERVICES)
        assert all(action["action"] == "set_manual" for action in plan)

    def test_never_touches_critical_services(self, offline_kb):
        plan = offline_planner.build_action_plan(offline_kb, SERVICES)
        assert "rpcss" not in {action["id"] for action in plan}

    def test_empty_component_list_yields_nothing(self, offline_kb):
        assert offline_planner.build_action_plan(offline_kb, {"services": []}) == []
        assert offline_planner.build_action_plan(offline_kb, None) == []

    def test_actions_carry_russian_explanation(self, offline_kb):
        plan = offline_planner.build_action_plan(offline_kb, SERVICES)
        assert all(action["user_explanation_ru"] for action in plan)

    def test_cleanup_allows_only_high_safety(self, offline_kb):
        junk = {"temp": {}, "pip": {}, "browser": {}}
        plan = offline_planner.build_cleanup_plan(offline_kb, junk)

        assert plan["temp"]["clean"] is True
        assert plan["pip"]["clean"] is False
        assert plan["browser"]["clean"] is False

    def test_cleanup_ignores_categories_not_found_by_scanner(self, offline_kb):
        plan = offline_planner.build_cleanup_plan(offline_kb, {"temp": {}})
        assert set(plan) == {"temp"}

    def test_unknown_category_is_not_cleaned(self, offline_kb):
        plan = offline_planner.build_cleanup_plan(offline_kb, {"mystery": {}})
        assert plan["mystery"]["clean"] is False

    def test_build_plan_returns_expected_shape(self, offline_kb):
        plan = offline_planner.build_plan(offline_kb, SERVICES, {"temp": {}})
        assert set(plan) == {"action_plan", "cleanup_plan"}


@pytest.fixture
def kb_dir(tmp_path: Path, offline_kb) -> Path:
    directory = tmp_path / "kb"
    directory.mkdir()
    for name, payload in (
        ("optimization_rules", offline_kb["optimization_rules"]),
        ("cleanup_rules", offline_kb["cleanup_rules"]),
    ):
        (directory / f"{name}.yaml").write_text(
            yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8"
        )
    return directory


def make_core(kb_dir: Path, *, force_offline: bool) -> WinSpectorCore:
    return WinSpectorCore({"kb_path": kb_dir, "force_offline": force_offline, "app_config": {}})


class TestModeDetection:
    def test_forced_offline_disables_ai(self, kb_dir, monkeypatch):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        core = make_core(kb_dir, force_offline=True)
        assert core._detect_ai_mode() is False

    def test_missing_key_disables_ai(self, kb_dir, monkeypatch):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.setattr(
            "winspector.core.analyzer.AIBase.has_api_key", staticmethod(lambda: False)
        )
        core = make_core(kb_dir, force_offline=False)
        assert core._detect_ai_mode() is False

    def test_present_key_enables_ai(self, kb_dir, monkeypatch):
        monkeypatch.setattr(
            "winspector.core.analyzer.AIBase.has_api_key", staticmethod(lambda: True)
        )
        core = make_core(kb_dir, force_offline=False)
        assert core._detect_ai_mode() is True


def wire_system(core: WinSpectorCore, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Подменяет все обращения к системе; возвращает журнал вызовов."""
    calls: dict[str, Any] = {}

    async def restore_point():
        calls["restore_point"] = True

    async def profile():
        return {"hardware": {}}

    async def components():
        return SERVICES

    async def junk(*_args, **_kwargs):
        return {"temp": {"files_to_delete": [], "folders_to_clean": []}}

    async def standard():
        return {"cleaned_size_bytes": 2048, "deleted_files_count": 3, "errors": 0}

    async def deep(decisions, report):
        calls["deep_decisions"] = decisions
        return {"cleaned_size_bytes": 1024, "deleted_files_count": 1, "errors": 0}

    async def empty_folders(extra_paths=None, app_dirs=None):
        return {"deleted_folders_count": 0, "errors": 0, "app_dirs_removed": []}

    async def execute(plan, progress=None):
        calls["executed"] = plan
        return {"completed": list(plan), "failed": [], "skipped": []}

    monkeypatch.setattr(core.windows_optimizer, "create_restore_point", restore_point)
    monkeypatch.setattr(core.user_profiler, "get_system_profile", profile)
    monkeypatch.setattr(core.windows_optimizer, "get_system_components", components)
    monkeypatch.setattr(core.smart_cleaner, "find_junk_files_deep", junk)
    monkeypatch.setattr(core.smart_cleaner, "perform_standard_cleanup", standard)
    monkeypatch.setattr(core.smart_cleaner, "perform_deep_cleanup", deep)
    monkeypatch.setattr(core.smart_cleaner, "cleanup_all_empty_folders_async", empty_folders)
    monkeypatch.setattr(core.windows_optimizer, "execute_action_plan", execute)
    return calls


class TestOfflineRun:
    """Полный сценарий без ИИ: система изменяется, отчёт выдаётся."""

    @pytest.fixture
    def wired_core(self, kb_dir, monkeypatch):
        core = make_core(kb_dir, force_offline=True)
        return core, wire_system(core, monkeypatch)

    async def test_completes_without_ai(self, wired_core):
        core, calls = wired_core

        report = await core.run_autonomous_optimization()
        await core.shutdown()

        assert calls["restore_point"] is True
        assert report, "отчёт должен быть сформирован"
        assert "без ИИ" in report

    async def test_no_network_call_is_attempted(self, wired_core, monkeypatch):
        """В офлайн-режиме обращений к Gemini быть не должно."""

        def explode(*args, **kwargs):
            raise AssertionError("сеть не должна использоваться в офлайн-режиме")

        monkeypatch.setattr("winspector.core.modules.ai_base.AIBase._get_client", explode)
        core, _ = wired_core

        await core.run_autonomous_optimization()
        await core.shutdown()

    async def test_plan_comes_from_knowledge_base(self, wired_core):
        core, calls = wired_core

        await core.run_autonomous_optimization()
        await core.shutdown()

        assert [item["id"] for item in calls["executed"]] == ["safeservice"]

    async def test_cleanup_uses_offline_decisions(self, wired_core):
        core, calls = wired_core

        await core.run_autonomous_optimization()
        await core.shutdown()

        assert calls["deep_decisions"] == {"temp": {"clean": True}}

    async def test_report_mentions_how_to_enable_ai(self, wired_core):
        core, _ = wired_core

        report = await core.run_autonomous_optimization()
        await core.shutdown()

        assert "Gemini" in report


class TestGracefulDegradation:
    """Сбой ИИ посреди сценария не должен обрывать работу."""

    async def test_ai_failure_falls_back_to_offline_plan(self, kb_dir, monkeypatch):
        monkeypatch.setattr(
            "winspector.core.analyzer.AIBase.has_api_key", staticmethod(lambda: True)
        )
        core = make_core(kb_dir, force_offline=False)

        async def failing_plan(*args, **kwargs):
            raise AIUnavailableError("квота исчерпана")

        async def profile_ok(*args, **kwargs):
            return ["Gamer"]

        monkeypatch.setattr(core.ai_analyzer, "generate_distillation_plan", failing_plan)
        monkeypatch.setattr(core.ai_communicator, "determine_user_profile", profile_ok)

        core.ai_enabled = True
        session: dict[str, Any] = {
            "system_components": SERVICES,
            "junk_files_report": {"temp": {}},
            "user_profile": ["Gamer"],
        }

        await core._step_generate_ai_plan(session, lambda value, text: None)

        assert core.ai_enabled is False, "режим должен переключиться на офлайн"
        assert [item["id"] for item in session["ai_plan"]["action_plan"]] == ["safeservice"]

    async def test_lost_connection_finishes_run_without_ai(self, kb_dir, monkeypatch):
        """
        Нет интернета — SDK выбрасывает `httpx.ConnectError`, не наследника
        `OSError`. Раньше эта ошибка проходила мимо обработчиков и обрывала
        сценарий сразу после точки восстановления.
        """
        import httpx

        class OfflineModels:
            async def generate_content(self, **_kwargs):
                raise httpx.ConnectError("нет сети")

        offline_client = type("C", (), {"aio": type("A", (), {"models": OfflineModels()})()})()
        monkeypatch.setattr(
            "winspector.core.modules.ai_base.AIBase._get_client",
            classmethod(lambda cls: offline_client),
        )

        async def instant_sleep(_delay=0, result=None):
            return result

        monkeypatch.setattr("asyncio.sleep", instant_sleep)
        monkeypatch.setattr(
            "winspector.core.analyzer.AIBase.has_api_key", staticmethod(lambda: True)
        )
        core = make_core(kb_dir, force_offline=False)
        calls = wire_system(core, monkeypatch)

        report = await core.run_autonomous_optimization()
        await core.shutdown()

        assert [item["id"] for item in calls["executed"]] == ["safeservice"]
        assert "ИИ в этот раз не ответил" in report
        assert "укажите бесплатный ключ" not in report
