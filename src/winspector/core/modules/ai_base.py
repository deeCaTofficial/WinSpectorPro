# src/winspector/core/modules/ai_base.py
"""
Базовый класс для взаимодействия с Google Gemini через SDK `google-genai`.

Инкапсулирует общую для всех ИИ-модулей логику:
- единый переиспользуемый клиент на всё приложение;
- запрос структурированного JSON через `response_schema` (SDK гарантирует
  синтаксически валидный ответ, поэтому ручной «починки» JSON больше нет);
- кеширование ответов с TTL;
- повторные попытки при временных сбоях сети/сервера;
- явные исключения вместо «тихого» возврата пустого результата.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import random
import time
from typing import Any, ClassVar

from google import genai
from google.genai import errors as genai_errors
from google.genai import types

from .. import credentials
from ..exceptions import (
    AIContentBlockedError,
    AIResponseError,
    AIUnavailableError,
)

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "gemini-2.5-flash"

# Приложение анализирует системные службы и пути к файлам. Штатные фильтры
# иногда принимают такие данные за «опасный контент», поэтому фильтры
# отключены: весь ввод формируется локально самим приложением.
_SAFETY_SETTINGS = [
    types.SafetySetting(category=category, threshold=types.HarmBlockThreshold.BLOCK_NONE)
    for category in (
        types.HarmCategory.HARM_CATEGORY_HARASSMENT,
        types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
        types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
        types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
    )
]


class AIBase:
    """
    Базовый класс для ИИ-модулей.

    Клиент `genai.Client` создаётся один раз на процесс и переиспользуется:
    он потокобезопасен и держит пул HTTP-соединений.
    """

    _client: ClassVar[genai.Client | None] = None

    def __init__(self, config: dict[str, Any], model_name: str | None = None) -> None:
        self.config: dict[str, Any] = config.get("app_config", {}) or {}
        self.model_name: str = (
            model_name or self.config.get("ai_model") or os.getenv("GEMINI_MODEL") or DEFAULT_MODEL
        )
        self._cache: dict[str, tuple[str, float]] = {}
        logger.info("%s инициализирован (модель: %s).", self.__class__.__name__, self.model_name)

    # --- Клиент -----------------------------------------------------------

    @classmethod
    def _get_client(cls) -> genai.Client:
        """Лениво создаёт и возвращает общий клиент Gemini."""
        if cls._client is None:
            # Ключ ищется в окружении, а при его отсутствии — в защищённом
            # хранилище, куда он попадает из окна настройки. Скачавшему
            # программу пользователю не нужно создавать файлы вручную.
            api_key = credentials.resolve_api_key()
            if not api_key:
                raise AIUnavailableError(
                    "Ключ Gemini не найден. Укажите его в окне настройки ИИ "
                    "или задайте переменную окружения GEMINI_API_KEY."
                )
            cls._client = genai.Client(api_key=api_key)
            logger.info("Клиент Google Gemini успешно создан.")
        return cls._client

    @staticmethod
    def has_api_key() -> bool:
        """Есть ли настроенный ключ. Не обращается к сети."""
        return credentials.has_api_key()

    @classmethod
    def reset_client(cls) -> None:
        """Сбрасывает общий клиент (используется в тестах и при смене ключа)."""
        cls._client = None

    async def ping(self) -> bool:
        """Проверяет доступность API. Возвращает False вместо исключения."""
        try:
            await self._generate(
                "ping",
                context="ping",
                use_cache=False,
                max_output_tokens=8,
                retries=0,
            )
            return True
        except Exception as exc:
            logger.warning("API Gemini недоступен: %s", exc)
            return False

    # --- Запросы ----------------------------------------------------------

    def _build_config(
        self,
        *,
        temperature: float | None,
        max_output_tokens: int | None,
        response_schema: Any | None,
        system_instruction: str | None,
    ) -> types.GenerateContentConfig:
        timeout_s = self.config.get("ai_request_timeout", 120)
        return types.GenerateContentConfig(
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            safety_settings=_SAFETY_SETTINGS,
            system_instruction=system_instruction,
            # `response_schema` переводит модель в режим строгого JSON —
            # ответ гарантированно разбирается `json.loads`.
            response_mime_type="application/json" if response_schema else None,
            response_schema=response_schema,
            http_options=types.HttpOptions(timeout=int(timeout_s * 1000)),
        )

    def _cache_key(self, prompt: str, context: str) -> str:
        raw = f"{self.model_name}|{context}|{prompt}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    async def _generate(
        self,
        prompt: str,
        context: str,
        *,
        use_cache: bool = True,
        temperature: float | None = None,
        max_output_tokens: int | None = None,
        response_schema: Any | None = None,
        system_instruction: str | None = None,
        retries: int = 2,
    ) -> str:
        """
        Отправляет запрос в Gemini и возвращает текст ответа.

        Raises:
            AIUnavailableError: сеть, таймаут, квота или ошибка сервера.
            AIContentBlockedError: ответ отклонён фильтрами безопасности.
            AIResponseError: пустой ответ или обрыв по лимиту токенов.
        """
        cache_key = self._cache_key(prompt, context)
        if use_cache and (cached := self._cache.get(cache_key)):
            text, created_at = cached
            if time.monotonic() - created_at < self.config.get("ai_cache_ttl", 3600):
                logger.info("Использован кешированный ответ для '%s'.", context)
                return text

        config = self._build_config(
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            response_schema=response_schema,
            system_instruction=system_instruction,
        )
        client = self._get_client()

        last_error: Exception | None = None
        for attempt in range(retries + 1):
            try:
                logger.debug("Запрос к ИИ (%s), попытка %d/%d.", context, attempt + 1, retries + 1)
                response = await client.aio.models.generate_content(
                    model=self.model_name, contents=prompt, config=config
                )
                text = self._extract_text(response, context)
                if use_cache:
                    self._cache[cache_key] = (text, time.monotonic())
                return text

            except (AIContentBlockedError, AIResponseError):
                # Повтор бессмысленен: проблема в самом запросе или в политике.
                raise
            except genai_errors.ClientError as exc:
                # 4xx: неверный ключ, превышена квота, некорректный запрос.
                raise AIUnavailableError(f"Запрос к Gemini отклонён ({context}): {exc}") from exc
            except (genai_errors.ServerError, asyncio.TimeoutError, OSError) as exc:
                last_error = exc
                if attempt < retries:
                    delay = 2**attempt + random.uniform(0, 0.5)
                    logger.warning(
                        "Временная ошибка Gemini (%s): %s. Повтор через %.1f с.",
                        context,
                        exc,
                        delay,
                    )
                    await asyncio.sleep(delay)

        raise AIUnavailableError(
            f"Не удалось получить ответ от Gemini ({context}) "
            f"после {retries + 1} попыток: {last_error}"
        ) from last_error

    @staticmethod
    def _extract_text(response: types.GenerateContentResponse, context: str) -> str:
        """Достаёт текст из ответа, различая блокировку и обрыв генерации."""
        feedback = getattr(response, "prompt_feedback", None)
        block_reason = getattr(feedback, "block_reason", None)
        if block_reason:
            raise AIContentBlockedError(
                f"Запрос '{context}' заблокирован фильтрами Gemini: {block_reason}.",
                block_reason=block_reason,
            )

        candidates = getattr(response, "candidates", None) or []
        if candidates:
            finish_reason = getattr(candidates[0], "finish_reason", None)
            if finish_reason == types.FinishReason.MAX_TOKENS:
                raise AIResponseError(
                    f"Ответ ИИ на '{context}' обрезан лимитом токенов. "
                    "Увеличьте max_output_tokens или сократите объём данных."
                )
            if finish_reason == types.FinishReason.SAFETY:
                raise AIContentBlockedError(
                    f"Ответ ИИ на '{context}' отклонён фильтрами безопасности.",
                    block_reason=finish_reason,
                )

        text = (getattr(response, "text", None) or "").strip()
        if not text:
            raise AIResponseError(f"Получен пустой ответ от ИИ для '{context}'.")
        return text

    async def _generate_json(
        self,
        prompt: str,
        context: str,
        *,
        response_schema: Any,
        **kwargs: Any,
    ) -> Any:
        """Запрашивает структурированный JSON и разбирает его."""
        text = await self._generate(prompt, context, response_schema=response_schema, **kwargs)
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            logger.error(
                "Не удалось разобрать JSON от ИИ (%s): %s. Ответ: %.500s",
                context,
                exc,
                text,
            )
            raise AIResponseError(f"ИИ вернул некорректный JSON для '{context}'.") from exc
