# tests/core/test_ai_analyzer_core.py
"""
Тесты для основной функциональности AIAnalyzer: инициализация, кеширование,
и успешное выполнение основного сценария генерации плана.
"""

import pytest
from unittest.mock import MagicMock
import os
import time

# Импортируем тестируемый класс. Фикстуры pytest найдет автоматически в conftest.py
from src.winspector.core.modules.ai_analyzer import AIAnalyzer

# Помечаем все тесты в этом файле как асинхронные
pytestmark = pytest.mark.asyncio


class TestAIAnalyzerInitialization:
    """Группа тестов, проверяющих инициализацию класса."""

    def test_initialization_succeeds_with_api_key(self, mock_config, mock_generative_model):
        """Проверяет, что инициализация проходит успешно при наличии API-ключа."""
        # GIVEN/WHEN
        # Просто создаем экземпляр. Наличие фикстуры mock_generative_model
        # уже гарантирует, что реальный API не будет вызван.
        analyzer = AIAnalyzer(config=mock_config)
        
        # THEN
        assert isinstance(analyzer, AIAnalyzer)
        # Проверяем, что модель была установлена
        assert analyzer.model is not None


    def test_initialization_fails_without_api_key(self, mocker):
        """Проверяет, что инициализация вызывает ValueError, если API-ключ отсутствует."""
        # GIVEN
        # Временно удаляем переменную окружения для этого теста
        mocker.patch.dict(os.environ, clear=True)
        
        # WHEN / THEN
        with pytest.raises(ValueError, match="Переменная окружения 'GEMINI_API_KEY' не найдена."):
            AIAnalyzer(config={})

    # Тест _ping_api убран, так как он был деталью реализации.
    # Мы тестируем публичное поведение, а не внутренние методы.
    # Новый conftest мокает сам GenerativeModel, делая этот тест избыточным.


class TestAIAnalyzerCoreFunctionality:
    """Группа тестов для основных методов класса."""

    # Тест determine_user_profile переехал в тесты для AICommunicator,
    # так как это его зона ответственности.
    
    async def test_generate_distillation_plan_calls_api(self, ai_analyzer: AIAnalyzer):
        """Проверяет, что метод генерации плана вызывает API с корректным промптом."""
        # GIVEN
        system_data = {"test_key": "test_value"}
        profile = "Gamer"
        kb = {"absolutely_critical": {}} # Упрощенная база знаний
        
        # Настраиваем ответ от мок-модели
        ai_analyzer.model.generate_content_async.return_value = MagicMock(text='{"action_plan": [], "cleanup_plan": {}}')
        
        # WHEN
        await ai_analyzer.generate_distillation_plan(system_data, profile, kb)

        # THEN
        # Проверяем, что API был вызван ровно один раз
        ai_analyzer.model.generate_content_async.assert_called_once()
        # Проверяем, что ключевые данные были переданы в промпт
        prompt_arg = ai_analyzer.model.generate_content_async.call_args[0][0]
        assert profile in prompt_arg
        assert '"test_key": "test_value"' in prompt_arg


class TestAIAnalyzerCache:
    """Группа тестов, проверяющих логику кеширования."""

    async def test_cache_is_used_on_second_call(self, ai_analyzer: AIAnalyzer):
        """Проверяет, что при повторном вызове с тем же промптом используется кеш."""
        # GIVEN
        prompt = "test_prompt_for_caching"
        # Настраиваем первый ответ
        ai_analyzer.model.generate_content_async.return_value = MagicMock(text="First Response")
        
        # WHEN (первый вызов)
        response1 = await ai_analyzer._get_response_with_cache(prompt, "ctx")
        
        # THEN (API был вызван)
        assert response1 == "First Response"
        ai_analyzer.model.generate_content_async.assert_called_once()

        # GIVEN (меняем то, что должен вернуть API)
        ai_analyzer.model.generate_content_async.return_value = MagicMock(text="Second Response")
        
        # WHEN (второй вызов с тем же промптом)
        response2 = await ai_analyzer._get_response_with_cache(prompt, "ctx")

        # THEN (API не был вызван снова, результат взят из кеша)
        assert response2 == "First Response"
        ai_analyzer.model.generate_content_async.assert_called_once() # Счетчик вызовов не изменился


    async def test_cache_is_ignored_when_expired(self, ai_analyzer: AIAnalyzer, mocker):
        """Проверяет, что кеш игнорируется, если истек его срок жизни (TTL)."""
        # GIVEN
        mock_time = mocker.patch('time.time')
        prompt = "test_prompt_for_expiration"
        
        # WHEN (первый вызов, кешируем ответ)
        mock_time.return_value = 1000.0
        ai_analyzer.model.generate_content_async.return_value = MagicMock(text="Expired Response")
        await ai_analyzer._get_response_with_cache(prompt, "ctx")

        # GIVEN (прошло больше времени, чем TTL, и API теперь вернет другой ответ)
        mock_time.return_value = 1000.0 + 3601.0  # TTL из mock_config = 3600
        ai_analyzer.model.generate_content_async.return_value = MagicMock(text="Fresh Response")
        
        # WHEN (повторный вызов)
        response = await ai_analyzer._get_response_with_cache(prompt, "ctx")

        # THEN (API был вызван снова, и мы получили свежий ответ)
        assert response == "Fresh Response"
        assert ai_analyzer.model.generate_content_async.call_count == 2