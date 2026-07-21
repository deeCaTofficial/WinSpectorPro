# tests/test_plan_validator.py
"""
Тесты слоя безопасности.

Это самый важный модуль проекта: он решает, какие действия из ответа модели
вообще дойдут до выполнения с правами администратора.
"""

from __future__ import annotations

from typing import Any

import pytest

from winspector.core.modules.plan_validator import (
    CRITICAL_SERVICES,
    PlanValidator,
    is_critical_service,
    is_critical_uwp,
    is_safe_identifier,
    normalize_profile,
    normalize_profiles,
    rule_targets,
)


def make_action(**overrides: Any) -> dict[str, Any]:
    """Корректное действие, в которое тест точечно вносит отклонение."""
    action = {
        "type": "service",
        "id": "MapsBroker",
        "action": "set_manual",
        "reason": "not needed",
        "user_explanation_ru": "Отключена служба карт.",
    }
    action.update(overrides)
    return action


# --- Нормализация профилей -------------------------------------------------


class TestProfileNormalization:
    """Рассогласование регистра профилей делало валидацию нерабочей."""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("PowerUser", "power_user"),
            ("power_user", "power_user"),
            ("POWER_USER", "power_user"),
            ("ContentCreator", "content_creator"),
            ("Gamer", "gamer"),
            ("gamer", "gamer"),
            ("  Developer  ", "developer"),
            ("office-worker", "office_worker"),
        ],
    )
    def test_camel_case_and_snake_case_converge(self, raw: str, expected: str):
        assert normalize_profile(raw) == expected

    def test_kb_and_model_vocabularies_match(self):
        """Значения из базы знаний и ответа модели должны совпасть."""
        from_kb = normalize_profiles(["power_user", "gamer"])
        from_model = normalize_profiles(["PowerUser", "Gamer"])
        assert from_kb == from_model
        assert from_kb & from_model

    def test_empty_input_is_safe(self):
        assert normalize_profiles(None) == set()
        assert normalize_profiles([]) == set()
        assert normalize_profiles(["", "   "]) == set()


# --- Проверка идентификаторов ---------------------------------------------


class TestIdentifierSafety:
    """Шаблон идентификатора — первый барьер против инъекции в PowerShell."""

    @pytest.mark.parametrize(
        "value",
        ["MapsBroker", "wuauserv", "Microsoft.WindowsStore", "Some-Service_1", "a" * 256],
    )
    def test_accepts_real_identifiers(self, value: str):
        assert is_safe_identifier(value)

    @pytest.mark.parametrize(
        "value",
        [
            "Maps'; Remove-Item C:\\ -Recurse -Force; '",  # инъекция кавычкой
            "svc; shutdown /s",
            "svc && calc",
            "svc | Out-File",
            "svc\nStop-Computer",
            "svc$(whoami)",
            "svc`whoami`",
            "службы",  # не ASCII
            "with space",
            "",
            "a" * 257,
            None,
            123,
            ["MapsBroker"],
        ],
    )
    def test_rejects_dangerous_or_malformed(self, value: Any):
        assert not is_safe_identifier(value)


class TestCriticalDenylist:
    """Жёсткий список зашит в код и не зависит от базы знаний."""

    @pytest.mark.parametrize(
        "service",
        ["RpcSs", "rpcss", "DcomLaunch", "WinDefend", "vss", "swprv", "Winmgmt", "gpsvc"],
    )
    def test_critical_services_blocked_case_insensitively(self, service: str):
        assert is_critical_service(service)

    def test_ordinary_service_not_blocked(self):
        assert not is_critical_service("MapsBroker")

    def test_restore_point_dependencies_are_protected(self):
        """Приложение само зависит от vss/swprv — их отключение недопустимо."""
        assert {"vss", "swprv"} <= CRITICAL_SERVICES

    @pytest.mark.parametrize(
        "package",
        [
            "Microsoft.WindowsStore",
            "microsoft.windowsstore_12.0_x64__8wekyb3d8bbwe",
            "Microsoft.DesktopAppInstaller",
            "Microsoft.SecHealthUI",
        ],
    )
    def test_critical_uwp_matched_by_prefix(self, package: str):
        assert is_critical_uwp(package)

    def test_ordinary_uwp_not_blocked(self):
        assert not is_critical_uwp("Microsoft.BingWeather")


class TestRuleTargets:
    """Из `Svc_MapsBroker` нужно получить реальное имя службы."""

    def test_strips_synthetic_prefix(self):
        assert rule_targets({"id": "Svc_MapsBroker"}) == {"mapsbroker"}

    def test_explicit_targets_take_priority(self):
        rule = {"id": "Svc_Bluetooth", "targets": ["bthserv", "BluetoothUserService"]}
        assert rule_targets(rule) == {"bthserv", "bluetoothuserservice"}

    def test_missing_id_yields_nothing(self):
        assert rule_targets({}) == set()


# --- Валидация плана действий ---------------------------------------------


class TestActionPlanValidation:
    def test_valid_action_passes(self, knowledge_base):
        validator = PlanValidator(knowledge_base, ["HomeUser"])
        result = validator.validate({"action_plan": [make_action()], "cleanup_plan": {}})
        assert len(result["action_plan"]) == 1
        assert result["action_plan"][0]["id"] == "MapsBroker"

    def test_critical_service_is_rejected(self, knowledge_base):
        validator = PlanValidator(knowledge_base, ["HomeUser"])
        result = validator.validate(
            {
                "action_plan": [make_action(id="RpcSs", action="disable")],
                "cleanup_plan": {},
            }
        )
        assert result["action_plan"] == []
        assert any("критическая служба" in r.lower() for r in validator.rejected)

    def test_injection_attempt_is_rejected(self, knowledge_base):
        """Идентификатор с кавычкой не должен дойти до генератора команд."""
        validator = PlanValidator(knowledge_base, ["HomeUser"])
        result = validator.validate(
            {
                "action_plan": [make_action(id="Maps'; Remove-Item C:\\ -Recurse; '")],
                "cleanup_plan": {},
            }
        )
        assert result["action_plan"] == []

    def test_kb_critical_rule_is_enforced(self, knowledge_base):
        """`safety: critical` в базе знаний блокирует действие."""
        validator = PlanValidator(knowledge_base, ["HomeUser"])
        result = validator.validate(
            {
                "action_plan": [make_action(id="LegacyCritical", action="disable")],
                "cleanup_plan": {},
            }
        )
        assert result["action_plan"] == []

    def test_protected_for_profile_blocks_disable(self, knowledge_base):
        """Служба, защищённая для геймера, не отключается у геймера."""
        validator = PlanValidator(knowledge_base, ["Gamer"])
        result = validator.validate(
            {
                "action_plan": [make_action(id="SteamHelper", action="disable")],
                "cleanup_plan": {},
            }
        )
        assert result["action_plan"] == []

    def test_protected_service_still_allows_set_manual(self, knowledge_base):
        """Защита касается только необратимых действий disable/remove."""
        validator = PlanValidator(knowledge_base, ["Gamer"])
        result = validator.validate(
            {
                "action_plan": [make_action(id="SteamHelper", action="set_manual")],
                "cleanup_plan": {},
            }
        )
        assert len(result["action_plan"]) == 1

    def test_protection_does_not_leak_to_other_profiles(self, knowledge_base):
        """У разработчика служба Steam не защищена и может быть отключена."""
        validator = PlanValidator(knowledge_base, ["Developer"])
        result = validator.validate(
            {
                "action_plan": [make_action(id="SteamHelper", action="disable")],
                "cleanup_plan": {},
            }
        )
        assert len(result["action_plan"]) == 1

    @pytest.mark.parametrize(
        "bad_action",
        [
            {"type": "service", "id": "X"},  # нет action
            {"type": "unknown", "id": "X", "action": "disable"},  # неизвестный тип
            {"type": "service", "id": "X", "action": "format_c"},  # неизвестное действие
            "не словарь",
            None,
            42,
        ],
    )
    def test_malformed_entries_are_dropped(self, knowledge_base, bad_action):
        validator = PlanValidator(knowledge_base, ["HomeUser"])
        result = validator.validate({"action_plan": [bad_action], "cleanup_plan": {}})
        assert result["action_plan"] == []

    def test_duplicates_collapse(self, knowledge_base):
        validator = PlanValidator(knowledge_base, ["HomeUser"])
        result = validator.validate(
            {
                "action_plan": [make_action(), make_action(id="mapsbroker")],
                "cleanup_plan": {},
            }
        )
        assert len(result["action_plan"]) == 1

    def test_unsafe_package_full_name_rejected(self, knowledge_base):
        validator = PlanValidator(knowledge_base, ["HomeUser"])
        result = validator.validate(
            {
                "action_plan": [
                    make_action(
                        type="uwp_app",
                        id="Some.App",
                        action="remove",
                        package_full_name="x'; calc; '",
                    )
                ],
                "cleanup_plan": {},
            }
        )
        assert result["action_plan"] == []

    def test_one_bad_entry_does_not_discard_good_ones(self, knowledge_base):
        """Спорный пункт отбрасывается, полезные — сохраняются."""
        validator = PlanValidator(knowledge_base, ["HomeUser"])
        result = validator.validate(
            {
                "action_plan": [
                    make_action(id="RpcSs"),
                    make_action(id="MapsBroker"),
                    make_action(id="DiagTrack"),
                ],
                "cleanup_plan": {},
            }
        )
        assert {a["id"] for a in result["action_plan"]} == {"MapsBroker", "DiagTrack"}

    def test_non_dict_plan_raises(self, knowledge_base):
        validator = PlanValidator(knowledge_base, ["HomeUser"])
        with pytest.raises(ValueError):
            validator.validate(["not", "a", "dict"])

    def test_non_list_action_plan_raises(self, knowledge_base):
        validator = PlanValidator(knowledge_base, ["HomeUser"])
        with pytest.raises(ValueError):
            validator.validate({"action_plan": {"a": 1}, "cleanup_plan": {}})

    def test_empty_plan_is_valid(self, knowledge_base):
        validator = PlanValidator(knowledge_base, ["HomeUser"])
        result = validator.validate({"action_plan": [], "cleanup_plan": {}})
        assert result == {"action_plan": [], "cleanup_plan": {}}


# --- Валидация плана очистки ----------------------------------------------


class TestCleanupPlanValidation:
    def test_known_scanned_category_approved(self, knowledge_base):
        validator = PlanValidator(
            knowledge_base, ["HomeUser"], known_junk_categories={"browser_cache"}
        )
        result = validator.validate(
            {"action_plan": [], "cleanup_plan": {"browser_cache": {"clean": True}}}
        )
        assert result["cleanup_plan"]["browser_cache"]["clean"] is True

    def test_unknown_category_rejected(self, knowledge_base):
        """Модель не может «придумать» категорию для удаления."""
        validator = PlanValidator(knowledge_base, ["HomeUser"])
        result = validator.validate(
            {"action_plan": [], "cleanup_plan": {"my_documents": {"clean": True}}}
        )
        assert "my_documents" not in result["cleanup_plan"]

    def test_category_absent_from_scan_rejected(self, knowledge_base):
        """Категория известна базе знаний, но сканер её не находил."""
        validator = PlanValidator(
            knowledge_base, ["HomeUser"], known_junk_categories={"browser_cache"}
        )
        result = validator.validate(
            {"action_plan": [], "cleanup_plan": {"python_pip_cache": {"clean": True}}}
        )
        assert "python_pip_cache" not in result["cleanup_plan"]

    def test_low_safety_blocked_for_developer(self, knowledge_base):
        """Кеш pip для разработчика — рабочие данные, а не мусор."""
        validator = PlanValidator(
            knowledge_base, ["Developer"], known_junk_categories={"python_pip_cache"}
        )
        result = validator.validate(
            {"action_plan": [], "cleanup_plan": {"python_pip_cache": {"clean": True}}}
        )
        assert result["cleanup_plan"]["python_pip_cache"]["clean"] is False

    def test_low_safety_allowed_for_home_user(self, knowledge_base):
        validator = PlanValidator(
            knowledge_base, ["HomeUser"], known_junk_categories={"python_pip_cache"}
        )
        result = validator.validate(
            {"action_plan": [], "cleanup_plan": {"python_pip_cache": {"clean": True}}}
        )
        assert result["cleanup_plan"]["python_pip_cache"]["clean"] is True

    def test_refusal_to_clean_is_always_honoured(self, knowledge_base):
        validator = PlanValidator(knowledge_base, ["HomeUser"])
        result = validator.validate(
            {"action_plan": [], "cleanup_plan": {"anything_at_all": {"clean": False}}}
        )
        assert result["cleanup_plan"]["anything_at_all"]["clean"] is False

    @pytest.mark.parametrize("bad", [{"clean": None}, {}, "yes", None, 1])
    def test_malformed_decisions_handled(self, knowledge_base, bad):
        validator = PlanValidator(knowledge_base, ["HomeUser"])
        result = validator.validate({"action_plan": [], "cleanup_plan": {"browser_cache": bad}})
        assert result["cleanup_plan"].get("browser_cache", {}).get("clean") is not True

    def test_non_dict_cleanup_plan_raises(self, knowledge_base):
        validator = PlanValidator(knowledge_base, ["HomeUser"])
        with pytest.raises(ValueError):
            validator.validate({"action_plan": [], "cleanup_plan": ["x"]})


class TestValidatorRobustness:
    """Валидатор не должен падать на повреждённой базе знаний."""

    def test_empty_knowledge_base(self):
        validator = PlanValidator({}, ["HomeUser"])
        result = validator.validate({"action_plan": [make_action(id="RpcSs")], "cleanup_plan": {}})
        # Даже без базы знаний жёсткий денилист продолжает работать.
        assert result["action_plan"] == []

    def test_malformed_rules_are_skipped(self):
        kb = {"optimization_rules": ["строка", None, {}, {"safety": "critical"}]}
        validator = PlanValidator(kb, ["HomeUser"])
        assert validator.validate({"action_plan": [], "cleanup_plan": {}}) is not None

    def test_rules_without_category_id_skipped(self):
        kb = {"cleanup_rules": [{"safety": "high"}, None, "x"]}
        validator = PlanValidator(kb, ["HomeUser"])
        assert validator.cleanup_rules == {}
