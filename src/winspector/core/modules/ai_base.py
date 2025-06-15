# src/winspector/core/modules/ai_base.py
"""
Содержит базовый класс для взаимодействия с API Google Generative AI.

Этот класс инкапсулирует общую логику для всех модулей, работающих с ИИ:
- Единоразовая конфигурация API.
- Отправка запросов с обработкой ошибок.
- Кеширование ответов.
- Проверка доступности API.
"""
import os
import logging
import time
import hashlib
import google.generativeai as genai
from typing import Dict, Any, Tuple

logger = logging.getLogger(__name__)


class AIBase:
    """
    Базовый класс для работы с API Gemini.
    Предоставляет общие методы для отправки запросов и кеширования.
    """
    
    # Статическая переменная для отслеживания статуса конфигурации
    _is_configured = False

    def __init__(self, config: Dict[str, Any], model_name: str = 'gemini-2.0-flash'):
        """
        Инициализирует базовый клиент для работы с ИИ.

        Args:
            config: Словарь конфигурации приложения.
            model_name: Имя модели Gemini, которую следует использовать.
        """
        self.config = config.get('app_config', {})
        self.cache: Dict[str, Tuple[str, float]] = {}
        
        # Конфигурируем API только один раз за все время работы приложения
        if not AIBase._is_configured:
            api_key = os.getenv("GEMINI_API_KEY")
            if not api_key:
                raise ValueError("Переменная окружения 'GEMINI_API_KEY' не найдена.")
            
            genai.configure(api_key=api_key)
            AIBase._is_configured = True
            logger.info("API Google Generative AI успешно сконфигурирован.")

        # Выбираем модель, имя которой можно переопределить в дочернем классе
        self.model = genai.GenerativeModel(model_name)
        
        # Проверяем доступность API при создании первого экземпляра
        # В реальном приложении это можно делать лениво, чтобы не замедлять запуск
        # self._ping_api() 
        logger.info(f"{self.__class__.__name__} успешно инициализирован.")

    def _ping_api(self):
        """Проверяет доступность API Gemini."""
        try:
            timeout = self.config.get('ai_ping_timeout', 10)
            logger.debug(f"Проверка доступности API Gemini с таймаутом {timeout}с...")
            self.model.generate_content("ping", request_options={'timeout': timeout})
            logger.info("API Gemini доступен.")
        except Exception as e:
            raise ConnectionError(f"Не удалось подключиться к API Gemini: {e}") from e

    async def _get_response_with_cache(
        self, prompt: str, context: str, use_cache: bool = True
    ) -> str:
        """
        Отправляет запрос в ИИ, используя кеширование и обработку ошибок.

        Args:
            prompt: Текст промпта для ИИ.
            context: Контекст запроса для логирования.
            use_cache: Использовать ли кеширование для этого запроса.

        Returns:
            Текстовый ответ от ИИ или пустой JSON-объект в случае ошибки.
        """
        prompt_hash = hashlib.md5(prompt.encode('utf-8')).hexdigest()
        if use_cache and (cached_response := self.cache.get(prompt_hash)):
            response_text, timestamp = cached_response
            if time.time() - timestamp < self.config.get('ai_cache_ttl', 3600):
                logger.info(f"Использование кэшированного ответа для '{context}'.")
                return response_text

        logger.debug(f"Отправка нового запроса в ИИ. Контекст: {context}")
        
        try:
            # Настройки безопасности для генерации контента
            # Можно вынести в config, если нужна гибкость
            safety_settings = {
                'HARM_CATEGORY_HARASSMENT': 'BLOCK_NONE',
                'HARM_CATEGORY_HATE_SPEECH': 'BLOCK_NONE',
                'HARM_CATEGORY_SEXUALLY_EXPLICIT': 'BLOCK_NONE',
                'HARM_CATEGORY_DANGEROUS_CONTENT': 'BLOCK_NONE',
            }
            generation_config = genai.types.GenerationConfig(
                # Увеличиваем максимальное количество токенов в ответе.
                # Для Gemini 1.5 Flash это значение может быть очень большим.
                max_output_tokens=65536
            )
            response = await self.model.generate_content_async(
                prompt,
                generation_config=generation_config,
                safety_settings=safety_settings
            )
            
            # Проверяем, был ли ответ заблокирован несмотря на настройки
            if not response.parts:
                logger.warning(
                    f"Ответ от ИИ для '{context}' был заблокирован. "
                    f"Причина: {response.prompt_feedback.block_reason}. "
                    f"Рейтинги безопасности: {response.prompt_feedback.safety_ratings}"
                )
                return "{}"  # Возвращаем пустой JSON

            response_text = response.text
            if use_cache:
                self.cache[prompt_hash] = (response_text, time.time())
            return response_text

        except Exception as e:
            logger.error(f"Ошибка при запросе к API Gemini для '{context}': {e}", exc_info=True)
            # Возвращаем пустой JSON, чтобы вышестоящий код мог gracefully handle it
            return "{}"