# tests/test_ai_layer.py
"""
Тесты слоя работы с Gemini.

Сеть не используется: подменяется `client.aio.models.generate_content`,
поэтому проверяется именно наша логика — кеш, повторы, разбор ответа
и преобразование ошибок SDK в исключения приложения.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx
import pytest
from google.genai import errors as genai_errors
from google.genai import types

from winspector.core.exceptions import (
    AIContentBlockedError,
    AIResponseError,
    AIUnavailableError,
)
from winspector.core.modules import ai_base
from winspector.core.modules.ai_analyzer import AIAnalyzer, strip_kb_noise
from winspector.core.modules.ai_base import AIBase, check_api_key, is_legacy_model
from winspector.core.modules.ai_communicator import AICommunicator, _format_bytes


class FakeResponse:
    """Минимальный дублёр GenerateContentResponse."""

    def __init__(
        self,
        text: str = "{}",
        block_reason: Any = None,
        finish_reason: Any = None,
    ) -> None:
        self.text = text
        self.prompt_feedback = type("PF", (), {"block_reason": block_reason})()
        self.candidates = [type("C", (), {"finish_reason": finish_reason})()]


class FakeModels:
    """Подменяет client.aio.models и записывает вызовы."""

    def __init__(self, responses: list[Any]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def generate_content(self, *, model, contents, config):
        self.calls.append({"model": model, "contents": contents, "config": config})
        result = self.responses.pop(0) if self.responses else FakeResponse()
        if isinstance(result, BaseException):
            raise result
        return result


@pytest.fixture
def patch_client(monkeypatch):
    """
    Возвращает функцию, подменяющую клиента заданными ответами.

    Паузы между повторами (1, 2, 4 с) здесь ни к чему: тесты проверяют
    логику повторов, а не хронометраж, поэтому ожидание всегда мгновенное.
    """
    monkeypatch.setattr("asyncio.sleep", _instant_sleep)

    def _patch(responses: list[Any]) -> FakeModels:
        fake_models = FakeModels(responses)
        fake_client = type("C", (), {"aio": type("A", (), {"models": fake_models})()})()
        monkeypatch.setattr(AIBase, "_get_client", classmethod(lambda cls: fake_client))
        return fake_models

    return _patch


def api_error(status: int, message: str, reason: str = "") -> genai_errors.APIError:
    """Создаёт ошибку SDK нужного класса."""
    cls = genai_errors.ClientError if status < 500 else genai_errors.ServerError
    return cls(status, {"error": {"message": message, "code": status, "status": reason}})


# --- Базовый слой ----------------------------------------------------------


class TestAIBaseGeneration:
    async def test_returns_text(self, ai_config, patch_client):
        patch_client([FakeResponse(text="привет")])
        assert await AIBase(ai_config)._generate("p", "ctx") == "привет"

    async def test_missing_api_key_is_explicit(self, ai_config, monkeypatch):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        AIBase.reset_client()
        with pytest.raises(AIUnavailableError, match="GEMINI_API_KEY"):
            await AIBase(ai_config)._generate("p", "ctx")

    async def test_blocked_prompt_raises(self, ai_config, patch_client):
        patch_client([FakeResponse(block_reason="SAFETY")])
        with pytest.raises(AIContentBlockedError):
            await AIBase(ai_config)._generate("p", "ctx")

    async def test_truncated_answer_raises(self, ai_config, patch_client):
        """Обрыв по лимиту токенов — не «пустой ответ», а отдельная ситуация."""
        patch_client([FakeResponse(text="{partial", finish_reason=types.FinishReason.MAX_TOKENS)])
        with pytest.raises(AIResponseError, match="токен"):
            await AIBase(ai_config)._generate("p", "ctx")

    async def test_empty_answer_raises(self, ai_config, patch_client):
        patch_client([FakeResponse(text="   ")])
        with pytest.raises(AIResponseError):
            await AIBase(ai_config)._generate("p", "ctx")

    async def test_client_error_is_not_retried(self, ai_config, patch_client):
        """4xx (неверный ключ, квота) — повтор бессмыслен."""
        fake = patch_client([api_error(403, "quota exceeded")])
        with pytest.raises(AIUnavailableError):
            await AIBase(ai_config)._generate("p", "ctx", retries=3)
        assert len(fake.calls) == 1

    async def test_server_error_is_retried_then_succeeds(
        self, ai_config, patch_client, monkeypatch
    ):
        monkeypatch.setattr("asyncio.sleep", _instant_sleep)
        fake = patch_client([api_error(503, "overloaded"), FakeResponse(text="готово")])

        result = await AIBase(ai_config)._generate("p", "ctx", retries=2)

        assert result == "готово"
        assert len(fake.calls) == 2

    async def test_retries_are_exhausted(self, ai_config, patch_client, monkeypatch):
        monkeypatch.setattr("asyncio.sleep", _instant_sleep)
        fake = patch_client([api_error(500, "boom") for _ in range(5)])

        with pytest.raises(AIUnavailableError):
            await AIBase(ai_config)._generate("p", "ctx", retries=2)

        assert len(fake.calls) == 3  # первая попытка + два повтора

    async def test_ping_reports_failure_without_raising(self, ai_config, patch_client):
        patch_client([api_error(403, "bad key")])
        assert await AIBase(ai_config).ping() is False

    async def test_ping_accepts_answer_cut_by_thinking(self, ai_config, patch_client):
        """Рассуждающая модель может истратить крошечный лимит на размышления."""
        patch_client([FakeResponse(text="", finish_reason=types.FinishReason.MAX_TOKENS)])
        assert await AIBase(ai_config).ping() is True

    async def test_lost_connection_is_retried_then_unavailable(self, ai_config, patch_client):
        """`httpx.ConnectError` не наследует OSError, но это тот же сетевой сбой."""
        fake = patch_client([httpx.ConnectError("нет сети") for _ in range(3)])

        with pytest.raises(AIUnavailableError):
            await AIBase(ai_config)._generate("p", "ctx", retries=2)

        assert len(fake.calls) == 3


class TestModelSettings:
    @pytest.mark.parametrize(
        ("model", "legacy"),
        [
            ("gemini-2.5-flash", True),
            ("gemini-2.0-flash", True),
            ("models/gemini-2.5-pro", True),
            ("gemini-3.8-flash", False),
            ("gemini-3.5-flash-lite", False),
            ("gemini-flash-latest", False),
        ],
    )
    def test_generation_is_detected_from_name(self, model, legacy):
        assert is_legacy_model(model) is legacy

    def test_default_model_is_available_to_new_keys(self, monkeypatch):
        monkeypatch.delenv("GEMINI_MODEL", raising=False)
        assert AIBase({}).model_name == "gemini-3.8-flash"

    def test_environment_overrides_default_model(self, monkeypatch):
        monkeypatch.setenv("GEMINI_MODEL", "gemini-2.5-flash")
        assert AIBase({}).model_name == "gemini-2.5-flash"

    async def test_gemini_3_keeps_default_temperature(self, ai_config, patch_client):
        """Google не советует снижать температуру Gemini 3: модель может зацикливаться."""
        fake = patch_client([FakeResponse(text="ok")])

        await AIBase(ai_config)._generate("p", "ctx", temperature=0.2, thinking_level="low")

        config = fake.calls[0]["config"]
        assert config.temperature is None
        assert config.thinking_config.thinking_level == types.ThinkingLevel.LOW

    async def test_legacy_model_keeps_temperature_without_thinking_level(self, patch_client):
        fake = patch_client([FakeResponse(text="ok")])
        config = {"app_config": {"ai_model": "gemini-2.5-flash"}}

        await AIBase(config)._generate("p", "ctx", temperature=0.2, thinking_level="low")

        sent = fake.calls[0]["config"]
        assert sent.temperature == 0.2
        assert sent.thinking_config is None


class TestAIBaseCaching:
    async def test_identical_prompt_hits_cache(self, ai_config, patch_client):
        fake = patch_client([FakeResponse(text="раз"), FakeResponse(text="два")])
        ai = AIBase(ai_config)

        first = await ai._generate("prompt", "ctx")
        second = await ai._generate("prompt", "ctx")

        assert first == second == "раз"
        assert len(fake.calls) == 1

    async def test_cache_can_be_bypassed(self, ai_config, patch_client):
        fake = patch_client([FakeResponse(text="раз"), FakeResponse(text="два")])
        ai = AIBase(ai_config)

        await ai._generate("prompt", "ctx", use_cache=False)
        await ai._generate("prompt", "ctx", use_cache=False)

        assert len(fake.calls) == 2

    async def test_different_context_is_cached_separately(self, ai_config, patch_client):
        fake = patch_client([FakeResponse(text="a"), FakeResponse(text="b")])
        ai = AIBase(ai_config)

        await ai._generate("prompt", "ctx_one")
        await ai._generate("prompt", "ctx_two")

        assert len(fake.calls) == 2

    async def test_expired_cache_triggers_new_request(self, patch_client):
        fake = patch_client([FakeResponse(text="старое"), FakeResponse(text="новое")])
        ai = AIBase({"app_config": {"ai_cache_ttl": -1}})

        await ai._generate("prompt", "ctx")
        second = await ai._generate("prompt", "ctx")

        assert second == "новое"
        assert len(fake.calls) == 2


class TestStructuredJson:
    async def test_schema_switches_response_to_json_mode(self, ai_config, patch_client):
        fake = patch_client([FakeResponse(text='{"ok": true}')])
        schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}}

        result = await AIBase(ai_config)._generate_json("p", "ctx", response_schema=schema)

        assert result == {"ok": True}
        assert fake.calls[0]["config"].response_mime_type == "application/json"

    async def test_invalid_json_raises_response_error(self, ai_config, patch_client):
        patch_client([FakeResponse(text="{это не json")])
        with pytest.raises(AIResponseError):
            await AIBase(ai_config)._generate_json("p", "ctx", response_schema={})


# --- Генерация плана -------------------------------------------------------


class TestAIAnalyzer:
    def test_strip_kb_noise_removes_provenance(self):
        rules = [{"id": "Svc_X", "safety": "high", "provenance": {"comment": "x" * 500}}]
        cleaned = strip_kb_noise(rules)
        assert cleaned == [{"id": "Svc_X", "safety": "high"}]

    def test_strip_kb_noise_skips_non_dicts(self):
        assert strip_kb_noise(["строка", None, {"id": "A"}]) == [{"id": "A"}]

    def test_cleanup_decisions_become_dictionary(self):
        raw = {
            "cleanup_decisions": [
                {"category_id": "temp", "clean": True},
                {"category_id": "cache", "clean": False},
            ]
        }
        assert AIAnalyzer._decisions_to_cleanup_plan(raw) == {
            "temp": {"clean": True},
            "cache": {"clean": False},
        }

    def test_legacy_cleanup_plan_format_still_read(self):
        raw = {"cleanup_plan": {"temp": {"clean": True}}}
        assert AIAnalyzer._decisions_to_cleanup_plan(raw) == {"temp": {"clean": True}}

    def test_malformed_decisions_are_skipped(self):
        raw = {
            "cleanup_decisions": ["x", None, {"clean": True}, {"category_id": "ok", "clean": True}]
        }
        assert AIAnalyzer._decisions_to_cleanup_plan(raw) == {"ok": {"clean": True}}

    async def test_plan_is_validated_before_return(self, ai_config, patch_client, knowledge_base):
        """Опасное предложение модели не должно дойти до вызывающего кода."""
        model_answer = json.dumps(
            {
                "action_plan": [
                    {
                        "type": "service",
                        "id": "RpcSs",
                        "action": "disable",
                        "reason": "unused",
                        "user_explanation_ru": "Отключим RPC.",
                    },
                    {
                        "type": "service",
                        "id": "MapsBroker",
                        "action": "set_manual",
                        "reason": "unused",
                        "user_explanation_ru": "Карты — по требованию.",
                    },
                ],
                "cleanup_decisions": [{"category_id": "browser_cache", "clean": True}],
            }
        )
        patch_client([FakeResponse(text=model_answer)])

        plan = await AIAnalyzer(ai_config).generate_distillation_plan(
            {"junk_files_report": {"browser_cache": {"total_size": 10}}},
            ["HomeUser"],
            knowledge_base,
        )

        assert [a["id"] for a in plan["action_plan"]] == ["MapsBroker"]
        assert plan["cleanup_plan"]["browser_cache"]["clean"] is True

    async def test_prompt_omits_provenance(self, ai_config, patch_client, knowledge_base):
        """Служебные поля не должны попадать в промпт и жечь токены."""
        knowledge_base["optimization_rules"][0]["provenance"] = {"note": "СЕКРЕТНЫЙ_МАРКЕР"}
        fake = patch_client([FakeResponse(text='{"action_plan": [], "cleanup_decisions": []}')])

        await AIAnalyzer(ai_config).generate_distillation_plan(
            {"junk_files_report": {}}, ["HomeUser"], knowledge_base
        )

        assert "СЕКРЕТНЫЙ_МАРКЕР" not in fake.calls[0]["contents"]

    async def test_non_object_answer_raises(self, ai_config, patch_client, knowledge_base):
        patch_client([FakeResponse(text="[1, 2, 3]")])
        with pytest.raises(AIResponseError):
            await AIAnalyzer(ai_config).generate_distillation_plan(
                {"junk_files_report": {}}, ["HomeUser"], knowledge_base
            )

    async def test_empty_plan_is_accepted(self, ai_config, patch_client, knowledge_base):
        patch_client([FakeResponse(text='{"action_plan": [], "cleanup_decisions": []}')])
        plan = await AIAnalyzer(ai_config).generate_distillation_plan(
            {"junk_files_report": {}}, ["HomeUser"], knowledge_base
        )
        assert plan == {"action_plan": [], "cleanup_plan": {}}


# --- Профили и отчёт -------------------------------------------------------


class TestAICommunicator:
    async def test_profiles_are_parsed(self, ai_config, patch_client):
        patch_client([FakeResponse(text='{"profiles": ["Developer", "Gamer"]}')])
        profiles = await AICommunicator(ai_config).determine_user_profile({}, {})
        assert profiles == ["Developer", "Gamer"]

    async def test_duplicates_are_collapsed(self, ai_config, patch_client):
        patch_client([FakeResponse(text='{"profiles": ["Gamer", "Gamer"]}')])
        assert await AICommunicator(ai_config).determine_user_profile({}, {}) == ["Gamer"]

    @pytest.mark.parametrize(
        "answer",
        ['{"profiles": []}', '{"profiles": "Gamer"}', '{"profiles": ["Wizard"]}', "{}"],
    )
    async def test_bad_profile_answer_falls_back(self, ai_config, patch_client, answer):
        patch_client([FakeResponse(text=answer)])
        assert await AICommunicator(ai_config).determine_user_profile({}, {}) == ["HomeUser"]

    async def test_api_failure_falls_back_to_home_user(self, ai_config, patch_client):
        """Недоступность ИИ не должна срывать оптимизацию."""
        patch_client([api_error(403, "no quota")])
        assert await AICommunicator(ai_config).determine_user_profile({}, {}) == ["HomeUser"]

    async def test_report_is_generated(self, ai_config, patch_client):
        patch_client([FakeResponse(text="## Готово")])
        report = await AICommunicator(ai_config).generate_final_report({}, [], ["Gamer"])
        assert "Готово" in report

    async def test_report_falls_back_offline(self, ai_config, patch_client):
        """
        Оптимизация уже выполнена — пользователь обязан увидеть результат
        даже без сети.
        """
        patch_client(
            [
                api_error(503, "unavailable"),
                api_error(503, "unavailable"),
                api_error(503, "unavailable"),
            ]
        )
        summary = {
            "cleanup": {"cleaned_size_bytes": 1024 * 1024 * 5},
            "debloat": {"completed": [{"user_explanation_ru": "Отключена служба карт."}]},
            "empty_folders": {"deleted_folders_count": 3},
        }

        report = await AICommunicator(ai_config).generate_final_report(summary, [], ["Gamer"])

        assert "5.0 МБ" in report
        assert "Отключена служба карт." in report
        assert "недоступен" in report

    def test_offline_report_states_skipped_files_and_categories(self):
        """
        Честный отчёт: занятые и свежие файлы не приписываются к
        освобождённому месту, а отложенные категории названы с причиной.
        """
        report = AICommunicator.build_offline_report(
            {
                "cleanup": {
                    "cleaned_size_bytes": 3 * 1024 * 1024,
                    "deleted_files_count": 12,
                    "deleted_folders_count": 2,
                    "skipped_files_count": 4,
                    "skipped_size_bytes": 1024 * 1024,
                    "skipped_categories": {"microsoft_edge_cache": "запущено: msedge.exe"},
                },
                "debloat": {"completed": []},
                "empty_folders": {"deleted_folders_count": 5},
            }
        )

        assert "3.0 МБ (12 файлов)" in report
        assert "Удалено пустых папок:** 7" in report
        assert "Пропущено:** 4 файлов (1.0 МБ)" in report
        assert "microsoft_edge_cache: запущено: msedge.exe" in report

    def test_offline_report_lists_removed_empty_app_dirs(self):
        report = AICommunicator.build_offline_report(
            {
                "empty_folders": {
                    "deleted_folders_count": 3,
                    "app_dirs_removed": [r"C:\Program Files\Android", r"C:\Program Files\Oracle"],
                }
            }
        )
        assert "Пустые папки удалённых программ: 2" in report
        assert r"C:\Program Files\Android" in report

    def test_offline_report_omits_skipped_section_when_nothing_skipped(self):
        report = AICommunicator.build_offline_report(
            {"cleanup": {"cleaned_size_bytes": 10, "deleted_files_count": 1}}
        )
        assert "Пропущено" not in report
        assert "Отложено" not in report

    def test_offline_report_handles_empty_summary(self):
        report = AICommunicator.build_offline_report({})
        assert "0 байт" in report

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (0, "0 байт"),
            (-5, "0 байт"),
            (512, "512 байт"),
            (1024, "1.0 КБ"),
            (1024 * 1024, "1.0 МБ"),
            (1024**3, "1.00 ГБ"),
        ],
    )
    def test_byte_formatting(self, value, expected):
        assert _format_bytes(value) == expected


async def _instant_sleep(_delay: float) -> None:
    """Убирает реальные задержки между повторами."""
    return None


def test_report_prompt_never_contains_full_leftover_paths():
    """Полный путь содержит имя учётной записи — модели уходят только имена папок."""
    from winspector.core.modules.ai_communicator import _format_leftovers_for_prompt

    line = _format_leftovers_for_prompt(
        {
            "quarantined_count": 2,
            "quarantined_size_bytes": 2048,
            "batch_dir": r"C:\Users\secret.user\AppData\Local\WinSpectorPro\Quarantine\1",
            "items": [
                {"original": r"C:\Users\secret.user\AppData\Roaming\GoneApp"},
                {"original": r"C:\ProgramData\Vendor\OldTool"},
            ],
        }
    )

    assert "secret.user" not in line
    assert "GoneApp" in line and "OldTool" in line


def test_report_prompt_names_empty_app_dirs_without_paths():
    from winspector.core.modules.ai_communicator import _format_empty_app_dirs_for_prompt

    line = _format_empty_app_dirs_for_prompt(
        {"app_dirs_removed": [r"C:\Users\secret.user\AppData\Local\GoneTool"]}
    )

    assert "secret.user" not in line
    assert "GoneTool" in line
    assert _format_empty_app_dirs_for_prompt({}) == ""


async def test_self_reflection_prompt_has_no_full_paths(ai_config, monkeypatch):
    """
    Регрессия: саморефлексия отправляла модели итог сессии целиком — с полными
    путями остатков и папки карантина, где есть имя учётной записи.
    """
    monkeypatch.setenv("USERPROFILE", r"C:\Users\secret.user")
    sent: list[str] = []

    async def capture(self, prompt, *args, **kwargs):
        sent.append(prompt)
        return "ok"

    monkeypatch.setattr(AICommunicator, "_generate", capture)

    await AICommunicator(ai_config).get_ai_suggestions_for_improvement(
        summary={
            "leftovers": {
                "batch_dir": r"C:\Users\secret.user\AppData\Local\WinSpectorPro\Quarantine\1",
                "items": [{"original": r"C:\Users\secret.user\AppData\Roaming\GoneApp"}],
            },
            "empty_folders": {"app_dirs_removed": [r"C:\Program Files\Hypixel Studios"]},
            "debloat": {"failed": [{"error": r"нет доступа к C:\Users\secret.user\x.log"}]},
        }
    )

    (prompt,) = sent
    assert "secret.user" not in prompt
    assert "GoneApp" in prompt and "Hypixel Studios" in prompt
    assert "%USERPROFILE%" in prompt


# --- Проверка ключа в окне настройки -------------------------------------------


@pytest.fixture
def fake_gemini(monkeypatch):
    """Подменяет SDK для `check_api_key`: запросы записываются, ответ задаётся."""
    requests: list[dict[str, Any]] = []
    outcome: dict[str, Any] = {"raise": None}

    class Models:
        def generate_content(self, *, model, contents, config):
            requests.append({"model": model, "config": config})
            if outcome["raise"] is not None:
                raise outcome["raise"]
            return FakeResponse(text="", finish_reason=types.FinishReason.MAX_TOKENS)

    class Client:
        def __init__(self, *, api_key, http_options):
            requests.append({"api_key": api_key, "timeout": http_options.timeout})
            self.models = Models()

    fake_genai = type("G", (), {"Client": Client})
    monkeypatch.setattr(ai_base, "_sdk", lambda: (fake_genai, types, genai_errors))
    return requests, outcome


KEY = "AIzaTest-only-fake-key-000000000000000"


class TestKeyCheck:
    def test_any_answer_means_key_works(self, fake_gemini):
        requests, _ = fake_gemini

        result = check_api_key(KEY)

        assert result.ok
        assert not result.warning
        assert requests[0]["api_key"] == KEY
        assert requests[1]["model"] == "gemini-3.8-flash"
        assert requests[1]["config"].max_output_tokens <= 32

    def test_checks_the_model_the_app_will_use(self, fake_gemini, monkeypatch):
        requests, _ = fake_gemini
        monkeypatch.setenv("GEMINI_MODEL", "gemini-3.5-flash-lite")

        check_api_key(KEY)

        assert requests[1]["model"] == "gemini-3.5-flash-lite"

    @pytest.mark.parametrize(
        ("error", "ok", "expected"),
        [
            (
                api_error(
                    400, "API key not valid. Please pass a valid API key.", "INVALID_ARGUMENT"
                ),
                False,
                "недействителен",
            ),
            (
                api_error(
                    400, "User location is not supported for the API use.", "FAILED_PRECONDITION"
                ),
                False,
                "стране",
            ),
            (api_error(403, "Permission denied.", "PERMISSION_DENIED"), False, "отказал"),
            (
                api_error(404, "models/gemini-3.8-flash is not found.", "NOT_FOUND"),
                False,
                "gemini-3.8-flash недоступна",
            ),
            (
                api_error(
                    429, "Quota exceeded, limit: 0, model: gemini-3.8-flash", "RESOURCE_EXHAUSTED"
                ),
                False,
                "недоступна",
            ),
            (
                api_error(429, "Resource has been exhausted.", "RESOURCE_EXHAUSTED"),
                True,
                "лимит",
            ),
            (api_error(503, "The model is overloaded.", "UNAVAILABLE"), False, "не отвечает"),
            (httpx.ConnectError("нет сети"), False, "Нет связи"),
        ],
    )
    def test_failure_is_explained(self, fake_gemini, error, ok, expected):
        _, outcome = fake_gemini
        outcome["raise"] = error

        result = check_api_key(KEY)

        assert result.ok is ok
        assert expected in result.message

    def test_quota_is_a_warning_not_a_failure(self, fake_gemini):
        _, outcome = fake_gemini
        outcome["raise"] = api_error(429, "Resource has been exhausted.", "RESOURCE_EXHAUSTED")

        assert check_api_key(KEY).warning is True

    def test_key_never_reaches_the_log(self, fake_gemini, caplog):
        _, outcome = fake_gemini
        outcome["raise"] = api_error(400, "API key not valid.", "INVALID_ARGUMENT")

        with caplog.at_level(logging.DEBUG):
            check_api_key(KEY)
            outcome["raise"] = None
            check_api_key(KEY)

        assert KEY not in caplog.text

    @pytest.mark.parametrize(
        ("error", "expected"),
        [
            (None, "The key works."),
            (
                api_error(400, "User location is not supported.", "FAILED_PRECONDITION"),
                "not available in your country",
            ),
            (httpx.ConnectError("offline"), "Cannot reach Google"),
        ],
    )
    def test_messages_follow_interface_language(self, fake_gemini, error, expected):
        _, outcome = fake_gemini
        outcome["raise"] = error

        assert expected in check_api_key(KEY, language="en").message
