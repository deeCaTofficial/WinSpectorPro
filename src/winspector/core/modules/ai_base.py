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
import functools
import hashlib
import json
import logging
import os
import random
import re
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar

from .. import credentials
from ..exceptions import (
    AIContentBlockedError,
    AIResponseError,
    AIUnavailableError,
)

if TYPE_CHECKING:
    from google import genai
    from google.genai import types

logger = logging.getLogger(__name__)

# Модели 2.5 Google открывает только тем, кто пользовался ими раньше: с ключом,
# созданным сегодня, запрос к ним отклоняется. Для новых проектов Google
# рекомендует 3.8 Flash — она есть и в бесплатном тарифе.
DEFAULT_MODEL = "gemini-3.8-flash"

# Проверка ключа — это один короткий запрос. Ответ модели не нужен, важен
# только статус: поэтому хватает нескольких токенов и короткого таймаута.
KEY_CHECK_TIMEOUT_S = 20
_PROBE_MAX_TOKENS = 16

_MODEL_GENERATION = re.compile(r"^(?:models/)?gemini-(\d+)(?:\.\d+)?-")


def configured_model() -> str:
    """Модель, которую использует приложение: `GEMINI_MODEL` или модель по умолчанию."""
    return os.getenv("GEMINI_MODEL") or DEFAULT_MODEL


def is_legacy_model(model: str) -> bool:
    """
    Модель поколения до Gemini 3.

    Gemini 3 рассуждает перед ответом, а Google настоятельно советует не
    менять ей температуру: ниже 1.0 модель может зацикливаться. Старым
    моделям настройки передаются как раньше. Неизвестные имена (например,
    `gemini-flash-latest`) считаются современными.
    """
    match = _MODEL_GENERATION.match(model)
    return bool(match) and int(match.group(1)) < 3


# SDK импортируется по первому обращению, а не при загрузке модуля. Его
# импорт — полсекунды (pydantic-модели `google.genai.types`, httpx): столько
# же, сколько весь остальной старт приложения. Пользователь без ключа не
# делает ни одного запроса к ИИ и платить за это не должен; дочерний процесс
# WMI — тем более.
@functools.cache
def _sdk() -> tuple[Any, Any, Any]:
    """Возвращает `(genai, types, errors)` из `google-genai`."""
    from google import genai
    from google.genai import errors, types

    return genai, types, errors


@functools.cache
def _network_errors() -> tuple[type[Exception], ...]:
    """
    Сетевые сбои запроса к Gemini.

    SDK не оборачивает ошибки транспорта: нет интернета — наружу выходит
    `httpx.ConnectError`, которая не наследует ни `OSError`, ни `TimeoutError`.
    Без неё обрыв связи ронял весь сценарий вместо перехода в режим без ИИ.
    """
    import httpx

    return (TimeoutError, OSError, httpx.TransportError)


@functools.cache
def _safety_settings() -> list[Any]:
    """
    Отключённые фильтры контента.

    Приложение анализирует системные службы и пути к файлам. Штатные фильтры
    иногда принимают такие данные за «опасный контент», а весь ввод
    формируется локально самим приложением.
    """
    _, types, _ = _sdk()
    return [
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
        self.model_name: str = model_name or self.config.get("ai_model") or configured_model()
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
            genai, _, _ = _sdk()
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
        """
        Проверяет доступность API. Возвращает False вместо исключения.

        Успех — любой ответ сервиса. Текст не разбирается: рассуждающая модель
        может потратить весь крошечный лимит на размышления и ответить пусто,
        и это всё равно значит, что ключ, модель и регион в порядке.
        """
        _, types, _ = _sdk()
        try:
            await self._get_client().aio.models.generate_content(
                model=self.model_name,
                contents="ping",
                config=types.GenerateContentConfig(max_output_tokens=_PROBE_MAX_TOKENS),
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
        thinking_level: str | None = None,
    ) -> types.GenerateContentConfig:
        _, types, _ = _sdk()
        timeout_s = self.config.get("ai_request_timeout", 120)
        thinking_config = None
        if not is_legacy_model(self.model_name):
            # Температура остаётся по умолчанию (1.0), как советует Google.
            # Размышления входят в `max_output_tokens`, поэтому для простых
            # задач их глубина снижается явно.
            temperature = None
            if thinking_level:
                thinking_config = types.ThinkingConfig(thinking_level=thinking_level)
        return types.GenerateContentConfig(
            temperature=temperature,
            thinking_config=thinking_config,
            max_output_tokens=max_output_tokens,
            safety_settings=_safety_settings(),
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
        thinking_level: str | None = None,
        retries: int = 2,
    ) -> str:
        """
        Отправляет запрос в Gemini и возвращает текст ответа.

        `temperature` действует только на модели до Gemini 3, `thinking_level`
        (`"low"`, `"medium"`, `"high"`) — только на Gemini 3 и новее.

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
            thinking_level=thinking_level,
        )
        client = self._get_client()
        _, _, genai_errors = _sdk()

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
            except (genai_errors.ServerError, *_network_errors()) as exc:
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
            _, types, _ = _sdk()
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


# --- Проверка ключа ------------------------------------------------------------


@dataclass(frozen=True)
class KeyCheckResult:
    """
    Итог проверки ключа: можно ли его сохранять и что сказать пользователю.

    `warning` — ключ рабочий, но о чём-то стоит предупредить (лимит исчерпан).
    """

    ok: bool
    message: str
    warning: bool = False


def check_api_key(
    api_key: str, model: str | None = None, *, language: str = "ru"
) -> KeyCheckResult:
    """
    Проверяет ключ одним коротким запросом к той модели, с которой будет
    работать приложение.

    Так неверный ключ, недоступная модель или неподдерживаемая страна видны
    сразу в окне настройки, а не после оптимизации, когда программа молча
    переходила в режим без ИИ.

    Функция синхронная: окно настройки вызывает её в фоновом потоке, ещё до
    запуска цикла asyncio. Ключ уходит только в Google и не попадает в журнал.
    Сообщение для пользователя возвращается на языке интерфейса (`"ru"`/`"en"`).
    """
    model = model or configured_model()
    english = language == "en"
    genai, types, genai_errors = _sdk()
    try:
        client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(timeout=KEY_CHECK_TIMEOUT_S * 1000),
        )
        # Ответ не разбирается: любой ответ сервиса значит, что ключ, модель
        # и регион в порядке, даже если модель не уложилась в лимит токенов.
        client.models.generate_content(
            model=model,
            contents="ping",
            config=types.GenerateContentConfig(max_output_tokens=_PROBE_MAX_TOKENS),
        )
    except genai_errors.APIError as exc:
        logger.warning("Проверка ключа Gemini: %s %s — %s", exc.code, exc.status, exc.message)
        return _explain_api_error(exc, model, english=english)
    except _network_errors() as exc:
        logger.warning("Проверка ключа Gemini: нет связи с сервером (%s).", type(exc).__name__)
        return KeyCheckResult(
            False,
            "Cannot reach Google. Check your internet connection and try again."
            if english
            else "Нет связи с сервером Google. Проверьте подключение к интернету "
            "и попробуйте ещё раз.",
        )
    except Exception as exc:
        logger.exception("Проверка ключа Gemini завершилась ошибкой.")
        name = type(exc).__name__
        return KeyCheckResult(
            False,
            f"Could not check the key ({name})."
            if english
            else f"Не удалось проверить ключ ({name}).",
        )

    logger.info("Ключ Gemini проверен: модель %s доступна.", model)
    return KeyCheckResult(True, "The key works." if english else "Ключ работает.")


def _explain_api_error(exc: Any, model: str, *, english: bool) -> KeyCheckResult:
    """Переводит ответ Gemini API в понятное пользователю объяснение."""
    code = exc.code or 0
    text = f"{exc.status or ''} {exc.message or ''}".lower()

    def say(russian: str, english_text: str) -> str:
        return english_text if english else russian

    if code == 400 and "location" in text:
        return KeyCheckResult(
            False,
            say(
                "Gemini API недоступен в вашей стране — это ограничение Google. "
                "Программа может работать без ИИ.",
                "Gemini API is not available in your country (a Google restriction). "
                "The app can work without AI.",
            ),
        )
    if code == 400 and "api key" in text:
        return KeyCheckResult(
            False,
            say(
                "Google не принял ключ: он недействителен или истёк. "
                "Скопируйте ключ из AI Studio ещё раз целиком.",
                "Google rejected the key: it is invalid or expired. "
                "Copy the full key from AI Studio again.",
            ),
        )
    if code == 403:
        return KeyCheckResult(
            False,
            say(
                "Google отказал в доступе: ключ отключён или ограничен, либо в его "
                "проекте выключен Gemini API. Создайте новый ключ в AI Studio.",
                "Google denied access: the key is disabled or restricted, or the Gemini API "
                "is turned off in its project. Create a new key in AI Studio.",
            ),
        )
    # «limit: 0» — модели нет в бесплатном тарифе этого ключа: ждать бесполезно.
    if code == 404 or (code == 429 and "limit: 0" in text):
        return KeyCheckResult(
            False,
            say(
                f"Модель {model} недоступна для этого ключа.",
                f"Model {model} is not available for this key.",
            ),
        )
    if code == 429:
        # Ключ рабочий, просто лимит запросов исчерпан — он обновится сам.
        return KeyCheckResult(
            True,
            say(
                "Ключ работает, но лимит бесплатных запросов сейчас исчерпан — "
                "ИИ заработает, когда лимит обновится.",
                "The key works, but its free request quota is used up for now. "
                "AI will work again when the quota resets.",
            ),
            warning=True,
        )
    if code >= 500:
        return KeyCheckResult(
            False,
            say(
                "Сервис Google сейчас не отвечает. Попробуйте ещё раз чуть позже "
                "или продолжите без ИИ.",
                "Google's service is not responding. Try again a bit later or continue without AI.",
            ),
        )
    return KeyCheckResult(
        False,
        say(
            f"Google отклонил ключ (ошибка {code}). Подробности — в журнале программы.",
            f"Google rejected the key (error {code}). Details are in the program log.",
        ),
    )
