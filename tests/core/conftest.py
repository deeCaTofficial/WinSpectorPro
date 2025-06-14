# tests/core/conftest.py
"""
Общие фикстуры для тестов модулей ядра (`src/winspector/core`).

Этот файл автоматически обнаруживается pytest и предоставляет фикстуры
для всех тестовых файлов в этой директории и ее поддиректориях.
"""

import pytest
import os
import wmi  # Импортируем для wmi.x_wmi
from unittest.mock import MagicMock, AsyncMock, PropertyMock
from typing import Dict, Any

# Устанавливаем фейковый API-ключ до импорта модулей, которые могут его использовать.
# Это гарантирует, что тесты не будут зависеть от реальных секретов в окружении.
os.environ['GEMINI_API_KEY'] = 'test_api_key_for_tests'

# --- Импорты тестируемых классов и их зависимостей ---
# Импортируем все здесь, чтобы избежать ошибок "is not defined" в фикстурах
from src.winspector.core.modules.wmi_base import WMIBase
from src.winspector.core.modules.ai_analyzer import AIAnalyzer
from src.winspector.core.modules.ai_communicator import AICommunicator
import google.generativeai as genai


# --- Фикстуры для всего приложения ---

@pytest.fixture(scope="session")
def mock_config() -> Dict[str, Any]:
    """
    Фикстура для конфигурации приложения.
    Создается один раз за всю тестовую сессию для повышения производительности.
    """
    return {
        'app_config': {
            'ai_ping_timeout': 5,
            'ai_cache_ttl': 3600,
        },
        'user_profiler_config': {}, # Пустая секция для UserProfiler
        'heuristic_rules': {} # Пустая секция для SmartCleaner
    }


# --- Фикстуры для мокирования внешних систем ---

@pytest.fixture
def mock_wmi_instance(mocker) -> MagicMock:
    """
    Общая фикстура для мокинга wmi.WMI().
    Заменяет свойство wmi_instance в базовом классе WMIBase,
    чтобы все дочерние классы (UserProfiler, WindowsOptimizer) использовали наш мок.
    """
    mock_instance = MagicMock()
    # Патчим свойство 'wmi_instance' в классе WMIBase
    mocker.patch.object(
        WMIBase, 'wmi_instance', new_callable=PropertyMock, return_value=mock_instance
    )
    return mock_instance


@pytest.fixture
def mock_generative_model(mocker) -> MagicMock:
    """
    Фикстура для мокинга `google.generativeai.GenerativeModel`.
    Заменяет реальный класс на мок и возвращает экземпляр модели,
    чтобы тесты не делали реальных сетевых запросов.
    """
    mock_model_instance = MagicMock()
    mock_model_instance.generate_content_async = AsyncMock()
    
    # Патчим класс в базовом модуле, чтобы все наследники его использовали
    mocker.patch.object(genai, 'GenerativeModel', return_value=mock_model_instance)
    
    return mock_model_instance


# --- Фикстуры для создания экземпляров тестируемых классов ---

@pytest.fixture
def ai_analyzer(mock_config: Dict[str, Any], mock_generative_model: MagicMock, mocker) -> AIAnalyzer:
    """
    Фикстура для создания готового к работе экземпляра AIAnalyzer с "заглушками".
    """
    # Мокаем genai.configure, чтобы избежать реальной конфигурации API
    mocker.patch.object(genai, 'configure')
    
    # Создаем экземпляр, который при инициализации подхватит нашу мок-модель
    analyzer = AIAnalyzer(config=mock_config)
    analyzer.model = mock_generative_model # Явно присваиваем для надежности
    return analyzer


@pytest.fixture
def ai_communicator(mock_config: Dict[str, Any], mock_generative_model: MagicMock, mocker) -> AICommunicator:
    """
    Фикстура для создания готового к работе экземпляра AICommunicator.
    """
    mocker.patch.object(genai, 'configure')
    
    communicator = AICommunicator(config=mock_config)
    communicator.model = mock_generative_model
    return communicator