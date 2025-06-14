# src/winspector/core/modules/ai_analyzer.py
"""
Модуль для взаимодействия с API Google Generative AI (Gemini).
Отвечает за принятие интеллектуальных решений, генерацию планов,
отчетов и предложений по улучшению.
"""

import os
import json
import logging
import time
import hashlib
import re
import google.generativeai as genai
from typing import Dict, Any, List, Tuple

logger = logging.getLogger(__name__)


class AIAnalyzer:
    """
    Основной класс для взаимодействия с ИИ.
    Инкапсулирует логику генерации промптов, отправки запросов,
    кеширования и валидации ответов.
    """
    
    def __init__(self, config: Dict[str, Any]):
        logger.info("Инициализация AIAnalyzer (Advanced)...")
        self.config = config.get('app_config', {})
        self.cache: Dict[str, Tuple[str, float]] = {}
        
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("Переменная окружения 'GEMINI_API_KEY' не найдена.")
        
        # Конфигурируем API только один раз
        if not getattr(genai, '_configured', False):
            genai.configure(api_key=api_key)
            genai._configured = True # Устанавливаем флаг, чтобы избежать повторной конфигурации

        self.model = genai.GenerativeModel('gemini-1.5-flash')
        self._ping_api()
        logger.info("AIAnalyzer успешно инициализирован и API доступен.")

    def _ping_api(self):
        """Проверяет доступность API Gemini при инициализации."""
        try:
            timeout = self.config.get('ai_ping_timeout', 10)
            logger.debug(f"Проверка доступности API Gemini с таймаутом {timeout}с...")
            self.model.generate_content("ping", request_options={'timeout': timeout})
        except Exception as e:
            raise ConnectionError(f"Не удалось подключиться к API Gemini: {e}") from e

    async def _get_response_with_cache(self, prompt: str, context: str, use_cache: bool = True) -> str:
        """Отправляет запрос в ИИ, используя кеширование."""
        prompt_hash = hashlib.md5(prompt.encode('utf-8')).hexdigest()
        if use_cache and (cached_response := self.cache.get(prompt_hash)):
            response_text, timestamp = cached_response
            if time.time() - timestamp < self.config.get('ai_cache_ttl', 3600):
                logger.info(f"Использование кэшированного ответа для '{context}'.")
                return response_text

        logger.debug(f"Отправка нового запроса в ИИ. Контекст: {context}")
        response = await self.model.generate_content_async(prompt)
        
        if not response.parts:
            logger.warning(f"Ответ от ИИ был заблокирован. Фидбек: {response.prompt_feedback}")
            return "{}" # Возвращаем пустой JSON, чтобы избежать ошибок

        response_text = response.text
        if use_cache:
            self.cache[prompt_hash] = (response_text, time.time())
        return response_text

    @staticmethod
    def _extract_json_from_response(text: str) -> Dict:
        """Надежно извлекает JSON объект из текстового ответа ИИ, удаляя обертку ```json."""
        # Ищем блок JSON, который может быть заключен в ```json ... ```
        match = re.search(r'```json\s*(\{.*\}|\[.*\])\s*```', text, re.DOTALL)
        
        # Если нашли блок в ```json, извлекаем его содержимое
        if match:
            json_text = match.group(1)
        else:
            # Если не нашли, предполагаем, что весь текст - это JSON
            json_text = text

        try:
            return json.loads(json_text)
        except json.JSONDecodeError as e:
            logger.error(f"Не удалось распарсить JSON. Ошибка: {e}. Текст для парсинга: {json_text}")
            raise ValueError(f"JSON-объект не найден или некорректен в ответе ИИ.") from e

    # --- Методы для генерации промптов ---

    def _create_profile_prompt(self, system_data: Dict, kb_config: Dict) -> str:
        """Создает промпт для определения профиля пользователя."""
        return f"""
        Analyze the user's system data to determine their primary profile from 'Gamer', 'Developer', 'Designer', 'OfficeWorker', 'HomeUser'.
        Base your decision on hardware specs, installed software keywords, and filesystem markers.
        Respond with ONLY ONE word in a JSON object: {{"profile": "..."}}.
        
        Profiler Configuration (keywords to look for):
        {json.dumps(kb_config, indent=2)}

        System Data:
        {json.dumps(system_data, indent=2, default=str)}
        """

    def _create_plan_prompt(self, system_data: Dict, profile: str, kb: Dict) -> str:
        """Создает промпт для генерации плана оптимизации."""
        return f"""
        You are an expert Windows optimization engineer. Your task is to create a safe and effective optimization plan.
        Your response MUST BE A SINGLE, VALID JSON OBJECT with two specific keys: "action_plan" and "cleanup_plan".

        **JSON STRUCTURE REQUIREMENTS:**
        - "action_plan": A flat list of action objects. Each object must have keys: "type", "id", "action", "reason", "user_explanation_ru".
        - "cleanup_plan": A dictionary where keys are cleanup categories from the knowledge base, and values are objects with a "clean": true/false key.

        **EXAMPLE of a valid response format:**
        {{
        "action_plan": [
            {{
            "type": "service",
            "id": "Fax",
            "action": "disable",
            "package_full_name": null,
            "reason": "Fax service is not needed for a HomeUser.",
            "user_explanation_ru": "Отключена ненужная служба факсов."
            }},
            {{
            "type": "uwp_app",
            "id": "Microsoft.YourPhone",
            "action": "remove",
            "package_full_name": "Microsoft.YourPhone_1.2.3_x64__abc",
            "reason": "Bloatware for a user without an Android phone.",
            "user_explanation_ru": "Удалено приложение для связи с телефоном."
            }}
        ],
        "cleanup_plan": {{
            "user_temp": {{"clean": true, "description": "User temporary files"}},
            "windows_temp": {{"clean": true, "description": "System temporary files"}},
            "prefetch": {{"clean": false, "description": "Prefetch files, keep for performance."}}
        }}
        }}

        USER PROFILE: {profile}

        KNOWLEDGE BASE (contains safety rules and recommendations):
        {json.dumps(kb.get("absolutely_critical", {}), indent=2)}

        SYSTEM SNAPSHOT (data to analyze):
        {json.dumps(system_data, indent=2, default=str)}

        Strictly follow all safety rules. Do not touch critical items. Provide ONLY the JSON object as a response.
        """

    def _create_report_prompt(self, summary: Dict, plan: List[Dict]) -> str:
        """Создает промпт для генерации финального отчета."""
        def format_bytes(b):
            gb, mb, kb = b / (1024**3), b / (1024**2), b / 1024
            if gb >= 1: return f"{gb:.2f} ГБ"
            if mb >= 1: return f"{mb:.1f} МБ"
            if kb >= 1: return f"{kb:.1f} КБ"
            return f"{b} байт" if b > 0 else "0 байт"
        
        cleaned_size = format_bytes(summary.get("cleanup", {}).get("cleaned_size_bytes", 0))
        debloat_summary = summary.get("debloat", {})
        actions_performed_str = "\n".join([f"✅ {action['user_explanation_ru']}" for action in plan if action.get("user_explanation_ru")])

        return f"""
        You are "WinSpector AI Communicator". Your job is to create a friendly, encouraging report in Russian Markdown.
        
        CONTEXT:
        The user has just completed a system optimization. The tone should be positive and celebratory. Use simple, clear language.
        
        METRICS:
        - Space freed: {cleaned_size}
        - Services disabled: {len(debloat_summary.get("disabled_services", []))}
        - UWP apps removed: {len(debloat_summary.get("removed_apps", []))}
        
        ACTIONS PERFORMED:
        {actions_performed_str}
        
        TASK:
        Create a concise, well-formatted report in Russian Markdown with an encouraging headline, a summary of key metrics, a list of actions, and a reassuring closing statement. Use emojis (✅, 🚀, 💪).
        """

    def _create_suggestions_prompt(self, **kwargs) -> str:
        """Создает промпт для генерации предложений по улучшению."""
        # Этот промпт очень большой, для краткости предположим, что он формируется здесь
        return f"""
        You are "WinSpector AI Architect", a lead developer reviewing an optimization session.
        Your goal is to suggest future improvements.
        
        SESSION ANALYSIS:
        {json.dumps(kwargs, indent=2, ensure_ascii=False)}
        
        TASK:
        Based on this session's data, suggest 3-5 concrete improvements for future versions.
        Respond in Russian Markdown.
        """

    # --- Публичные методы API ---

    async def determine_user_profile(self, system_data: Dict, kb_config: Dict) -> str:
        """Определяет профиль пользователя."""
        prompt = self._create_profile_prompt(system_data, kb_config)
        response_text = await self._get_response_with_cache(prompt, "determine_user_profile")
        try:
            profile_data = self._extract_json_from_response(response_text)
            profile = profile_data.get("profile", "HomeUser").strip()
            logger.info(f"ИИ определил профиль как: {profile}")
            return profile
        except (json.JSONDecodeError, ValueError) as e:
            logger.error(f"Не удалось определить профиль пользователя: {e}")
            return "HomeUser"

    async def generate_distillation_plan(self, system_data: Dict, profile: str, kb: Dict) -> Dict:
        """Генерирует и валидирует план оптимизации."""
        prompt = self._create_plan_prompt(system_data, profile, kb)
        response_text = await self._get_response_with_cache(prompt, "generate_distillation_plan")
        
        try:
            plan = self._extract_json_from_response(response_text)
            # Теперь _validate_plan не падает, а возвращает очищенный план
            safe_plan = self._validate_plan(plan, kb) 
            logger.debug("Получен и валидирован безопасный план от ИИ.")
            return safe_plan
        except (json.JSONDecodeError, ValueError) as e:
            logger.error(f"Не удалось распарсить или валидировать план от ИИ: {e}\nОтвет ИИ: {response_text}")
            # В случае ошибки возвращаем абсолютно пустой план
            return {"action_plan": [], "cleanup_plan": {}}

    def _validate_plan(self, plan: Dict, kb: Dict) -> Dict:
        """
        Проводит строгую валидацию плана от ИИ.
        Небезопасные действия удаляются из плана, а не вызывают ошибку.
        Возвращает очищенный, безопасный план.
        """
        if not isinstance(plan, dict) or "action_plan" not in plan or "cleanup_plan" not in plan:
            raise ValueError("План должен быть словарем с ключами 'action_plan' и 'cleanup_plan'.")

        action_plan = plan.get("action_plan", [])
        if not isinstance(action_plan, list):
            raise ValueError(f"'action_plan' должен быть списком, а не {type(action_plan).__name__}.")

        critical_services = {s.lower() for s in kb.get("absolutely_critical", {}).get("services", [])}
        critical_uwp_apps = {a.lower() for a in kb.get("absolutely_critical", {}).get("uwp_apps", [])}

        safe_actions = []
        for action in action_plan:
            # Проверяем базовую структуру
            if not isinstance(action, dict) or not {"type", "id", "action"}.issubset(action.keys()):
                logger.warning(f"Пропуск некорректно сформированного действия в плане: {action}")
                continue

            # Проверяем на критичность
            item_id_lower = str(action["id"]).lower()
            is_unsafe = False
            if action["type"] == "service" and item_id_lower in critical_services:
                logger.warning(f"ОТКЛОНЕНО небезопасное действие над критической службой: {action['id']}")
                is_unsafe = True
            if action["type"] == "uwp_app" and item_id_lower in critical_uwp_apps:
                logger.warning(f"ОТКЛОНЕНО небезопасное действие над критическим UWP-приложением: {action['id']}")
                is_unsafe = True
            
            if not is_unsafe:
                safe_actions.append(action)

        # Обновляем план только безопасными действиями
        plan["action_plan"] = safe_actions
        
        # Валидация cleanup_plan остается
        cleanup_plan = plan.get("cleanup_plan", {})
        if not isinstance(cleanup_plan, dict):
            raise ValueError(f"'cleanup_plan' должен быть словарем, а не {type(cleanup_plan).__name__}.")

        logger.info(f"Валидация плана завершена. Одобрено {len(safe_actions)} действий.")
        return plan

    async def generate_final_report(self, summary: Dict, plan: List[Dict]) -> str:
        """Генерирует финальный отчет для пользователя."""
        logger.info("Генерация финального отчета.")
        prompt = self._create_report_prompt(summary, plan)
        return await self._get_response_with_cache(prompt, "generate_final_report", use_cache=False)

    async def get_ai_suggestions_for_improvement(self, **kwargs) -> str:
        """Анализирует сессию и предлагает улучшения для разработчиков."""
        logger.info("Запрос к ИИ на саморефлексию и предложения по улучшению.")
        prompt = self._create_suggestions_prompt(**kwargs)
        # Этот запрос всегда должен быть свежим, не используем кэш
        response = await self.model.generate_content_async(prompt)
        return response.text