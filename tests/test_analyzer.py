# tests/test_analyzer.py
"""
Тесты оркестратора: порядок шагов, передача данных между модулями,
поведение при отмене и при сбоях.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
import yaml

from winspector.core.analyzer import WinSpectorCore
from winspector.core.exceptions import KnowledgeBaseError, RestorePointError


@pytest.fixture
def kb_dir(tmp_path: Path, optimization_rules, cleanup_rules) -> Path:
    """Каталог базы знаний на диске."""
    kb = tmp_path / "knowledge_base"
    kb.mkdir()
    (kb / "optimization_rules.yaml").write_text(
        yaml.safe_dump(optimization_rules, allow_unicode=True), encoding="utf-8"
    )
    (kb / "cleanup_rules.yaml").write_text(
        yaml.safe_dump(cleanup_rules, allow_unicode=True), encoding="utf-8"
    )
    return kb


@pytest.fixture
def core(kb_dir: Path, monkeypatch: pytest.MonkeyPatch) -> WinSpectorCore:
    """
    Ядро в режиме с включённым ИИ.

    Наличие ключа подменяется явно: иначе на машине без настроенного ключа
    ядро уходило бы в офлайн-режим, и тесты проверяли бы не тот сценарий.
    Работа без ИИ покрыта отдельно в `test_offline_mode.py`.
    """
    monkeypatch.setattr("winspector.core.analyzer.AIBase.has_api_key", staticmethod(lambda: True))
    return WinSpectorCore({"kb_path": kb_dir, "app_config": {"ai_cache_ttl": 60}})


class Recorder:
    """Записывает порядок шагов сценария."""

    def __init__(self) -> None:
        self.steps: list[str] = []

    def wire(self, core: WinSpectorCore, monkeypatch: pytest.MonkeyPatch) -> None:
        steps = self.steps

        async def restore_point():
            steps.append("restore_point")

        async def system_profile():
            steps.append("profile")
            return {"hardware": {}}

        async def determine_profile(data, config):
            steps.append("determine_profile")
            return ["Gamer"]

        async def components():
            steps.append("components")
            return {"services": [], "uwp_apps": []}

        async def junk():
            steps.append("scan_junk")
            return {
                "browser_cache": {"total_size": 100, "files_to_delete": [], "folders_to_clean": []}
            }

        async def plan(data, profiles, kb):
            steps.append("ai_plan")
            return {
                "action_plan": [
                    {
                        "type": "service",
                        "id": "MapsBroker",
                        "action": "set_manual",
                        "reason": "r",
                        "user_explanation_ru": "Карты по требованию.",
                    }
                ],
                "cleanup_plan": {"browser_cache": {"clean": True}},
            }

        async def standard_cleanup():
            steps.append("standard_cleanup")
            return {"cleaned_size_bytes": 1000, "deleted_files_count": 5, "errors": 0}

        async def deep_cleanup(decisions, report):
            steps.append("deep_cleanup")
            self.deep_cleanup_args = (decisions, report)
            return {"cleaned_size_bytes": 500, "deleted_files_count": 2, "errors": 0}

        async def empty_folders():
            steps.append("empty_folders")
            return {"deleted_folders_count": 1, "errors": 0}

        async def execute_plan(action_plan, progress=None):
            steps.append("execute_plan")
            self.executed_plan = action_plan
            return {"completed": list(action_plan), "failed": [], "skipped": []}

        async def report(summary, plan_items, profiles):
            steps.append("report")
            self.report_summary = summary
            return "## Отчёт"

        async def suggestions(**kwargs):
            return "предложения"

        monkeypatch.setattr(core.windows_optimizer, "create_restore_point", restore_point)
        monkeypatch.setattr(core.user_profiler, "get_system_profile", system_profile)
        monkeypatch.setattr(core.ai_communicator, "determine_user_profile", determine_profile)
        monkeypatch.setattr(core.windows_optimizer, "get_system_components", components)
        monkeypatch.setattr(core.smart_cleaner, "find_junk_files_deep", junk)
        monkeypatch.setattr(core.ai_analyzer, "generate_distillation_plan", plan)
        monkeypatch.setattr(core.smart_cleaner, "perform_standard_cleanup", standard_cleanup)
        monkeypatch.setattr(core.smart_cleaner, "perform_deep_cleanup", deep_cleanup)
        monkeypatch.setattr(core.smart_cleaner, "cleanup_all_empty_folders_async", empty_folders)
        monkeypatch.setattr(core.windows_optimizer, "execute_action_plan", execute_plan)
        monkeypatch.setattr(core.ai_communicator, "generate_final_report", report)
        monkeypatch.setattr(core.ai_communicator, "get_ai_suggestions_for_improvement", suggestions)


# --- Загрузка базы знаний --------------------------------------------------


class TestKnowledgeBaseLoading:
    def test_loads_and_merges_files(self, core):
        assert len(core.knowledge_base["optimization_rules"]) == 4
        assert len(core.knowledge_base["cleanup_rules"]) == 3

    def test_missing_directory_raises(self, tmp_path):
        with pytest.raises(KnowledgeBaseError, match="не найдена"):
            WinSpectorCore({"kb_path": tmp_path / "absent"})

    def test_missing_path_key_raises(self):
        with pytest.raises(KnowledgeBaseError, match="не задан"):
            WinSpectorCore({})

    def test_empty_directory_raises(self, tmp_path):
        empty = tmp_path / "kb"
        empty.mkdir()
        with pytest.raises(KnowledgeBaseError, match="yaml"):
            WinSpectorCore({"kb_path": empty})

    def test_broken_yaml_raises(self, tmp_path):
        kb = tmp_path / "kb"
        kb.mkdir()
        (kb / "broken.yaml").write_text("key: [unclosed", encoding="utf-8")
        with pytest.raises(KnowledgeBaseError):
            WinSpectorCore({"kb_path": kb})

    def test_scalar_yaml_rejected(self, tmp_path):
        kb = tmp_path / "kb"
        kb.mkdir()
        (kb / "scalar.yaml").write_text("просто строка", encoding="utf-8")
        with pytest.raises(KnowledgeBaseError, match="список или словарь"):
            WinSpectorCore({"kb_path": kb})


# --- Фильтрация правил -----------------------------------------------------


class TestRuleFiltering:
    def test_profile_matching_works_across_naming_styles(self, core):
        """`gamer` в базе знаний и `Gamer` от модели — один и тот же профиль."""
        filtered = core._filter_kb_for_profile(["Gamer"])
        ids = {rule["id"] for rule in filtered["optimization_rules"]}
        assert "Svc_SteamHelper" in ids  # relevant_profiles: [gamer]
        assert "Svc_Universal" in ids  # правило без профилей

    def test_power_user_rules_selected(self, core):
        filtered = core._filter_kb_for_profile(["PowerUser"])
        ids = {rule["id"] for rule in filtered["optimization_rules"]}
        assert "Svc_MapsBroker" in ids

    def test_unmatched_profile_keeps_universal_rules(self, core):
        """Правила без `relevant_profiles` подходят любому профилю."""
        filtered = core._filter_kb_for_profile(["AudioEngineer"])
        ids = {rule["id"] for rule in filtered["optimization_rules"]}
        assert ids == {"Svc_Universal", "Svc_LegacyCritical"}

    def test_falls_back_to_full_set_when_nothing_matches(self, core):
        """
        Если все правила привязаны к профилям и ни одно не подошло, модель
        получила бы пустую базу знаний — это ухудшает решения, поэтому
        отдаём весь набор (безопасность обеспечивает валидатор).
        """
        core.knowledge_base["optimization_rules"] = [
            {"id": "Svc_A", "relevant_profiles": ["gamer"]},
            {"id": "Svc_B", "relevant_profiles": ["power_user"]},
        ]
        filtered = core._filter_kb_for_profile(["AudioEngineer"])
        assert len(filtered["optimization_rules"]) == 2

    def test_result_is_never_empty(self, core):
        for profile in ("Gamer", "Developer", "HomeUser", "Streamer", "Designer"):
            filtered = core._filter_kb_for_profile([profile])
            assert filtered["optimization_rules"], f"пусто для {profile}"

    def test_profiler_config_falls_back_to_code_defaults(self, core):
        """Секции нет ни в одном YAML — должна подставиться резервная."""
        config = core._profiler_config()
        assert "app_keywords" in config
        assert "Gamer" in config["app_keywords"]


# --- Полный сценарий -------------------------------------------------------


class TestFullRun:
    async def test_steps_run_in_the_expected_order(self, core, monkeypatch):
        recorder = Recorder()
        recorder.wire(core, monkeypatch)

        report = await core.run_autonomous_optimization()
        await core.shutdown()

        assert report == "## Отчёт"
        steps = recorder.steps
        # Точка восстановления — строго до любых изменений.
        assert steps[0] == "restore_point"
        assert steps.index("ai_plan") < steps.index("execute_plan")
        assert steps.index("ai_plan") < steps.index("deep_cleanup")
        assert steps.index("standard_cleanup") < steps.index("deep_cleanup")
        assert steps[-1] == "report"

    async def test_scan_report_is_passed_to_deep_cleanup(self, core, monkeypatch):
        """
        Регрессия: очистка получала только решения ИИ без путей и потому
        не удаляла ничего.
        """
        recorder = Recorder()
        recorder.wire(core, monkeypatch)

        await core.run_autonomous_optimization()
        await core.shutdown()

        decisions, report = recorder.deep_cleanup_args
        assert decisions == {"browser_cache": {"clean": True}}
        assert "browser_cache" in report
        assert "files_to_delete" in report["browser_cache"]

    async def test_summary_sums_both_cleanups(self, core, monkeypatch):
        recorder = Recorder()
        recorder.wire(core, monkeypatch)

        await core.run_autonomous_optimization()
        await core.shutdown()

        assert recorder.report_summary["cleanup"]["cleaned_size_bytes"] == 1500

    async def test_progress_is_monotonic_and_bounded(self, core, monkeypatch):
        recorder = Recorder()
        recorder.wire(core, monkeypatch)
        values: list[int] = []

        await core.run_autonomous_optimization(
            progress_callback=lambda value, text: values.append(value)
        )
        await core.shutdown()

        assert values == sorted(values), "прогресс не должен идти назад"
        assert values[-1] == 100
        assert all(0 <= v <= 100 for v in values)

    async def test_restore_point_failure_aborts_before_changes(self, core, monkeypatch):
        recorder = Recorder()
        recorder.wire(core, monkeypatch)

        async def failing():
            raise RestorePointError("защита системы отключена")

        monkeypatch.setattr(core.windows_optimizer, "create_restore_point", failing)

        with pytest.raises(RestorePointError):
            await core.run_autonomous_optimization()

        assert "execute_plan" not in recorder.steps
        assert "deep_cleanup" not in recorder.steps

    async def test_cancellation_between_steps(self, core, monkeypatch):
        recorder = Recorder()
        recorder.wire(core, monkeypatch)

        # Отмена запрашивается сразу после первой проверки.
        state = {"cancelled": False}

        async def restore_point():
            recorder.steps.append("restore_point")
            state["cancelled"] = True

        monkeypatch.setattr(core.windows_optimizer, "create_restore_point", restore_point)

        with pytest.raises(asyncio.CancelledError):
            await core.run_autonomous_optimization(is_cancelled=lambda: state["cancelled"])

        assert "execute_plan" not in recorder.steps

    async def test_cancellation_flag_false_runs_to_completion(self, core, monkeypatch):
        recorder = Recorder()
        recorder.wire(core, monkeypatch)

        report = await core.run_autonomous_optimization(is_cancelled=lambda: False)
        await core.shutdown()

        assert report == "## Отчёт"

    async def test_works_without_any_callbacks(self, core, monkeypatch):
        """Ядро не должно требовать GUI для работы."""
        Recorder().wire(core, monkeypatch)
        assert await core.run_autonomous_optimization() == "## Отчёт"
        await core.shutdown()


class TestComponentCache:
    async def test_second_scan_uses_cache(self, core, monkeypatch):
        calls = {"n": 0}

        async def components():
            calls["n"] += 1
            return {"services": [], "uwp_apps": []}

        async def junk():
            return {}

        monkeypatch.setattr(core.windows_optimizer, "get_system_components", components)
        monkeypatch.setattr(core.smart_cleaner, "find_junk_files_deep", junk)

        session: dict[str, Any] = {}
        await core._step_collect_data(session, lambda v, t: None)
        await core._step_collect_data(session, lambda v, t: None)

        assert calls["n"] == 1

    async def test_expired_cache_rescans(self, core, monkeypatch):
        from datetime import datetime, timedelta

        calls = {"n": 0}

        async def components():
            calls["n"] += 1
            return {"services": [], "uwp_apps": []}

        async def junk():
            return {}

        monkeypatch.setattr(core.windows_optimizer, "get_system_components", components)
        monkeypatch.setattr(core.smart_cleaner, "find_junk_files_deep", junk)

        session: dict[str, Any] = {}
        await core._step_collect_data(session, lambda v, t: None)
        core._last_scan_time = datetime.now() - timedelta(minutes=core.CACHE_TTL_MINUTES + 1)
        await core._step_collect_data(session, lambda v, t: None)

        assert calls["n"] == 2


class TestShutdown:
    async def test_shutdown_without_tasks_is_safe(self, core):
        await core.shutdown()

    async def test_background_task_failure_does_not_break_shutdown(self, core):
        async def failing():
            raise RuntimeError("фоновая ошибка")

        core._spawn_background(failing())
        await core.shutdown()  # не должно бросать

    async def test_self_reflection_failure_is_swallowed(self, core, monkeypatch):
        async def failing(**kwargs):
            raise RuntimeError("ИИ недоступен")

        monkeypatch.setattr(core.ai_communicator, "get_ai_suggestions_for_improvement", failing)
        await core._run_ai_self_reflection({})  # не должно бросать
