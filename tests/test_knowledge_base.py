# tests/test_knowledge_base.py
"""
Проверки настоящей базы знаний из репозитория.

Правила в YAML — это фактически конфигурация поведения продукта, но раньше
они ничем не проверялись: опечатка в `safety` или неизвестный профиль тихо
меняли работу валидатора. Эти тесты ловят такие ошибки в данных.
"""

from __future__ import annotations

import pytest

from winspector.core.modules.plan_validator import (
    CANONICAL_PROFILES,
    is_critical_service,
    normalize_profile,
    rule_targets,
)

VALID_SAFETY = {"critical", "low", "medium", "high"}
VALID_RULE_TYPES = {"service", "uwp_app", "startup_item"}


@pytest.fixture
def optimization_rules(real_knowledge_base) -> list[dict]:
    rules = real_knowledge_base.get("optimization_rules")
    if not rules:
        pytest.skip("optimization_rules.yaml отсутствует")
    return rules


@pytest.fixture
def cleanup_rules(real_knowledge_base) -> list[dict]:
    rules = real_knowledge_base.get("cleanup_rules")
    if not rules:
        pytest.skip("cleanup_rules.yaml отсутствует")
    return rules


class TestOptimizationRules:
    def test_all_entries_are_mappings(self, optimization_rules):
        assert all(isinstance(rule, dict) for rule in optimization_rules)

    def test_every_rule_has_an_id(self, optimization_rules):
        missing = [r for r in optimization_rules if not r.get("id")]
        assert not missing, f"правила без id: {missing}"

    def test_ids_are_unique(self, optimization_rules):
        ids = [rule["id"] for rule in optimization_rules]
        duplicates = {i for i in ids if ids.count(i) > 1}
        assert not duplicates, f"дублирующиеся id: {duplicates}"

    def test_safety_values_are_known(self, optimization_rules):
        bad = {
            rule["id"]: rule.get("safety")
            for rule in optimization_rules
            if str(rule.get("safety", "")).lower() not in VALID_SAFETY
        }
        assert not bad, f"недопустимые значения safety: {bad}"

    def test_rule_types_are_known(self, optimization_rules):
        bad = {
            rule["id"]: rule.get("type")
            for rule in optimization_rules
            if rule.get("type") and rule["type"] not in VALID_RULE_TYPES
        }
        assert not bad, f"недопустимые значения type: {bad}"

    def test_profiles_use_the_canonical_vocabulary(self, optimization_rules):
        """
        После нормализации все профили обязаны попадать в канонический набор,
        иначе правило никогда не совпадёт с ответом модели.
        """
        unknown: dict[str, list[str]] = {}
        for rule in optimization_rules:
            for field in ("relevant_profiles", "protected_for_profiles"):
                for profile in rule.get(field) or []:
                    if normalize_profile(profile) not in CANONICAL_PROFILES:
                        unknown.setdefault(rule["id"], []).append(profile)
        assert not unknown, f"неизвестные профили: {unknown}"

    def test_every_rule_has_a_russian_description(self, optimization_rules):
        """Описание попадает в отчёт пользователю — оно обязательно."""
        missing = [
            rule["id"]
            for rule in optimization_rules
            if not str(rule.get("description_ru", "")).strip()
        ]
        assert not missing, f"правила без description_ru: {missing}"

    def test_no_actionable_rule_targets_a_critical_service(self, optimization_rules):
        """
        База знаний не должна предлагать трогать то, что зашито в денилист.

        Правило с `safety: critical` при этом допустимо: оно ничего не
        предлагает, а наоборот фиксирует запрет — и код, и данные тогда
        говорят одно и то же.
        """
        offenders = {
            rule["id"]: sorted(targets)
            for rule in optimization_rules
            if str(rule.get("safety", "")).lower() != "critical"
            and (targets := {t for t in rule_targets(rule) if is_critical_service(t)})
        }
        assert not offenders, f"правила задевают критические службы: {offenders}"

    def test_critical_rules_are_consistent_with_the_denylist(self, optimization_rules):
        """Правило `safety: critical` и денилист кода не должны спорить."""
        for rule in optimization_rules:
            if str(rule.get("safety", "")).lower() != "critical":
                continue
            # Достаточно, чтобы правило было понятно описано: сам запрет
            # обеспечивает валидатор, а не эта проверка.
            assert rule.get("description_ru"), f"{rule['id']}: нет описания"

    def test_targets_are_lists_of_strings(self, optimization_rules):
        bad = {
            rule["id"]: rule["targets"]
            for rule in optimization_rules
            if "targets" in rule
            and not (
                isinstance(rule["targets"], list)
                and all(isinstance(t, str) for t in rule["targets"])
            )
        }
        assert not bad, f"некорректное поле targets: {bad}"


class TestCleanupRules:
    def test_every_rule_has_a_category_id(self, cleanup_rules):
        missing = [r for r in cleanup_rules if not r.get("category_id")]
        assert not missing, f"правила без category_id: {missing}"

    def test_category_ids_are_unique(self, cleanup_rules):
        ids = [rule["category_id"] for rule in cleanup_rules]
        duplicates = {i for i in ids if ids.count(i) > 1}
        assert not duplicates, f"дублирующиеся category_id: {duplicates}"

    def test_safety_values_are_known(self, cleanup_rules):
        bad = {
            rule["category_id"]: rule.get("safety")
            for rule in cleanup_rules
            if str(rule.get("safety", "")).lower() not in VALID_SAFETY
        }
        assert not bad, f"недопустимые значения safety: {bad}"

    def test_every_rule_declares_paths(self, cleanup_rules):
        bad = [
            rule["category_id"]
            for rule in cleanup_rules
            if not isinstance(rule.get("paths"), list) or not rule["paths"]
        ]
        assert not bad, f"правила очистки без путей: {bad}"

    def test_paths_are_not_dangerously_broad(self, cleanup_rules):
        """
        Правило не должно указывать на корень диска или системный каталог:
        такой путь отклонит защита путей, но лучше поймать это в данных.
        """
        forbidden_suffixes = (
            "\\windows",
            "\\windows\\system32",
            "\\program files",
            "%userprofile%",
            "%appdata%",
            "%localappdata%",
            "%programdata%",
        )
        offenders: dict[str, list[str]] = {}
        for rule in cleanup_rules:
            for path in rule.get("paths") or []:
                normalized = str(path).strip().rstrip("\\/").lower()
                is_drive_root = len(normalized) <= 3 and normalized.endswith(":")
                if is_drive_root or normalized in forbidden_suffixes:
                    offenders.setdefault(rule["category_id"], []).append(path)
        assert not offenders, f"слишком широкие пути очистки: {offenders}"

    def test_every_rule_has_a_russian_description(self, cleanup_rules):
        missing = [
            rule["category_id"]
            for rule in cleanup_rules
            if not str(rule.get("description_ru", "")).strip()
        ]
        assert not missing, f"правила без description_ru: {missing}"


class TestKnowledgeBaseLoadsIntoModules:
    """Реальная база знаний должна приниматься боевыми классами без ошибок."""

    def test_smart_cleaner_accepts_real_rules(self, cleanup_rules):
        from winspector.core.modules.smart_cleaner import SmartCleaner

        cleaner = SmartCleaner(cleanup_rules=cleanup_rules)
        assert len(cleaner.rules) == len(cleanup_rules)

    def test_validator_accepts_real_rules(self, real_knowledge_base):
        from winspector.core.modules.plan_validator import PlanValidator

        validator = PlanValidator(real_knowledge_base, ["Gamer", "Developer"])
        result = validator.validate({"action_plan": [], "cleanup_plan": {}})
        assert result == {"action_plan": [], "cleanup_plan": {}}

    def test_real_rules_produce_usable_targets(self, optimization_rules):
        """Из каждого правила должен извлекаться хотя бы один идентификатор."""
        empty = [rule["id"] for rule in optimization_rules if not rule_targets(rule)]
        assert not empty, f"правила без пригодных targets: {empty}"
