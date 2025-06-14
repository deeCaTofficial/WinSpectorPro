# tests/core/test_ai_analyzer_validation.py
"""
Тесты для проверки логики валидации и безопасности AIAnalyzer.

Проверяется, что система корректно обрабатывает некорректные,
неполные или небезопасные ответы от Искусственного Интеллекта.
"""

import pytest
import json
from unittest.mock import MagicMock, ANY

# Импортируем тестируемый класс. Фикстуры pytest найдет автоматически в conftest.py
from src.winspector.core.modules.ai_analyzer import AIAnalyzer

# Помечаем все тесты в этом файле как асинхронные
pytestmark = pytest.mark.asyncio


class TestPlanValidationAndSafety:
    """Группа тестов, проверяющих валидацию JSON-плана от ИИ."""

    async def test_generate_plan_succeeds_with_valid_response(self, ai_analyzer: AIAnalyzer):
        """Проверяет, что полностью валидный ответ от ИИ успешно парсится и возвращается."""
        # GIVEN
        valid_plan = {
            "action_plan": [{
                "type": "service",
                "id": "TestService",
                "action": "disable",
                "reason": "Test reason",
                "user_explanation_ru": "Тестовое объяснение для пользователя."
            }],
            "cleanup_plan": {"user_temp": {"clean": True}}
        }
        # Настраиваем мок-модель, чтобы она вернула валидный JSON
        ai_analyzer.model.generate_content_async.return_value = MagicMock(text=f"```json\n{json.dumps(valid_plan)}\n```")
        
        # WHEN
        # Передаем пустую базу знаний, так как план не содержит ничего критического
        plan = await ai_analyzer.generate_distillation_plan({}, "Gamer", {})
        
        # THEN
        assert plan == valid_plan
        ai_analyzer.model.generate_content_async.assert_called_once()

    @pytest.mark.parametrize(
        "invalid_text, reason",
        [
            ("это не json", "невалидный JSON"),
            (json.dumps({"wrong_key": []}), "отсутствует ключ 'action_plan'"),
            (json.dumps({"action_plan": "не список", "cleanup_plan": {}}), "'action_plan' не является списком"),
            (json.dumps({"action_plan": [{"id": "X"}], "cleanup_plan": {}}), "элемент в 'action_plan' не содержит всех ключей"),
        ]
    )
    async def test_generate_plan_returns_empty_plan_on_malformed_json(
        self, ai_analyzer: AIAnalyzer, mocker, invalid_text: str, reason: str
    ):
        """Проверяет, что при структурно невалидном ответе от ИИ возвращается пустой план."""
        # GIVEN
        ai_analyzer.model.generate_content_async.return_value = MagicMock(text=invalid_text)
        mock_log_error = mocker.patch("src.winspector.core.modules.ai_analyzer.logger.error")

        # WHEN
        plan = await ai_analyzer.generate_distillation_plan({}, "Gamer", {})
        
        # THEN
        # Главная проверка: результат должен быть пустым планом
        assert plan == {"action_plan": [], "cleanup_plan": {}}
        # Второстепенная проверка: в лог должна была записаться ошибка
        mock_log_error.assert_called_once_with(ANY, exc_info=True)
        assert reason in mock_log_error.call_args[0][0].lower()

    async def test_validate_plan_removes_unsafe_actions(self, ai_analyzer: AIAnalyzer, mocker):
        """
        Проверяет, что _validate_plan удаляет небезопасные действия, но оставляет безопасные.
        Это ключевой тест новой логики.
        """
        # GIVEN
        # План от ИИ, содержащий одно безопасное и одно небезопасное действие
        unsafe_plan = {
            "action_plan": [
                {
                    "type": "service",
                    "id": "SafeService", # Безопасная служба
                    "action": "disable", "reason": "r", "user_explanation_ru": "e"
                },
                {
                    "type": "uwp_app",
                    "id": "Microsoft.WindowsStore", # Критическое приложение
                    "action": "remove", "reason": "r", "user_explanation_ru": "e"
                }
            ],
            "cleanup_plan": {}
        }
        
        # База знаний, определяющая, что небезопасно
        kb = {
            "absolutely_critical": {
                "uwp_apps": ["Microsoft.WindowsStore"]
            }
        }
        
        mock_log_warning = mocker.patch("src.winspector.core.modules.ai_analyzer.logger.warning")
        ai_analyzer.model.generate_content_async.return_value = MagicMock(text=json.dumps(unsafe_plan))
        
        # WHEN
        result_plan = await ai_analyzer.generate_distillation_plan({}, "Gamer", kb)

        # THEN
        # 1. План не пустой
        assert result_plan is not None
        # 2. В итоговом плане осталось только одно, безопасное действие
        assert len(result_plan["action_plan"]) == 1
        assert result_plan["action_plan"][0]["id"] == "SafeService"
        # 3. Было записано предупреждение в лог об отклоненном действии
        mock_log_warning.assert_called_once()
        assert "ОТКЛОНЕНО небезопасное действие" in mock_log_warning.call_args[0][0]
        assert "Microsoft.WindowsStore" in mock_log_warning.call_args[0][0]