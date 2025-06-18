# src/winspector/core/modules/ai_analyzer.py
"""
Модуль для взаимодействия с API Google Generative AI (Gemini).
Отвечает за принятие интеллектуальных решений и генерацию планов.
"""

import os
import json
import logging
import time
import hashlib
import re
import google.generativeai as genai
from typing import Dict, Any, List, Tuple

from .ai_base import AIBase
from .ai_communicator import AICommunicator # Импортируем для _extract_json

logger = logging.getLogger(__name__)

class ContentBlockedError(Exception):
    """Исключение, выбрасываемое, когда ответ от API заблокирован."""
    def __init__(self, message, prompt_feedback):
        super().__init__(message)
        self.prompt_feedback = prompt_feedback

class _PlanValidator:
    """Внутренний класс, инкапсулирующий всю логику валидации плана от ИИ."""
    def __init__(self, full_kb: Dict[str, Any], user_profiles: List[str]):
        self.user_profiles = user_profiles
        self.optimization_rules = {rule['id'].lower(): rule for rule in full_kb.get('optimization_rules', [])}
        self.cleanup_rules = {rule['category_id']: rule for rule in full_kb.get('cleanup_rules', [])}
        
        self.critical_items = {
            id for id, rule in self.optimization_rules.items() if rule.get('safety') == 'critical'
        }
        self.profile_relevant_items = {
            id for id, rule in self.optimization_rules.items()
            if any(p in rule.get('relevant_profiles', []) for p in self.user_profiles)
        }

    def validate(self, plan: Dict) -> Dict:
        """Проводит полную, многоуровневую валидацию плана."""
        if not isinstance(plan, dict) or "action_plan" not in plan or "cleanup_plan" not in plan:
            raise ValueError("План должен быть словарем с ключами 'action_plan' и 'cleanup_plan'.")

        plan["action_plan"] = self._validate_action_plan(plan.get("action_plan", []))
        plan["cleanup_plan"] = self._validate_cleanup_plan(plan.get("cleanup_plan", {}))
        
        logger.info(f"Валидация плана завершена. Одобрено {len(plan['action_plan'])} действий.")
        return plan

    def _validate_action_plan(self, action_plan: List[Dict]) -> List[Dict]:
        if not isinstance(action_plan, list):
            raise ValueError(f"'action_plan' должен быть списком, а не {type(action_plan).__name__}.")

        safe_actions = []
        for action in action_plan:
            if not isinstance(action, dict) or not {"type", "id", "action"}.issubset(action.keys()):
                logger.warning(f"Пропуск некорректно сформированного действия в плане: {action}")
                continue

            item_id_lower = str(action["id"]).lower()
            
            # Уровень 1: Проверка на критичность
            if item_id_lower in self.critical_items:
                logger.warning(f"ОТКЛОНЕНО небезопасное действие над критическим компонентом: {action['id']}")
                continue
            
            # Уровень 2: Проверка на релевантность профилю
            # Запрещаем 'disable' или 'remove' для релевантных профилю служб
            if action['action'] in ['disable', 'remove'] and item_id_lower in self.profile_relevant_items:
                logger.warning(f"ОТКЛОНЕНО действие '{action['action']}' над компонентом '{action['id']}', "
                               f"так как он важен для профилей {self.user_profiles}.")
                continue
            
            safe_actions.append(action)
        return safe_actions

    # ### УЛУЧШЕНИЕ: Валидация плана очистки ###
    def _validate_cleanup_plan(self, cleanup_plan: Dict) -> Dict:
        """Валидирует план очистки с учетом профиля пользователя."""
        if not isinstance(cleanup_plan, dict):
            raise ValueError(f"'cleanup_plan' должен быть словарем, а не {type(cleanup_plan).__name__}.")
        
        safe_cleanup_plan = {}
        for category_id, decision in cleanup_plan.items():
            if not isinstance(decision, dict) or 'clean' not in decision:
                logger.warning(f"Пропуск некорректной записи в cleanup_plan для '{category_id}'")
                continue
            
            # Если ИИ решил не чистить, мы с этим соглашаемся
            if not decision['clean']:
                safe_cleanup_plan[category_id] = decision
                continue

            rule = self.cleanup_rules.get(category_id)
            if not rule:
                logger.warning(f"ИИ предложил очистку для неизвестной категории '{category_id}'. Отклонено.")
                continue

            # Проверяем безопасность для чувствительных профилей
            is_sensitive_profile = any(p in ["Developer", "ContentCreator", "AudioEngineer"] for p in self.user_profiles)
            
            if rule.get('safety') == 'low' and is_sensitive_profile:
                logger.warning(f"ОТКЛОНЕНА очистка '{category_id}' с низким уровнем безопасности для профиля {self.user_profiles}.")
                safe_cleanup_plan[category_id] = {"clean": False}
            else:
                safe_cleanup_plan[category_id] = decision
                
        return safe_cleanup_plan


class AIAnalyzer(AIBase):
    """
    Основной AI-модуль, отвечающий за анализ данных и генерацию плана оптимизации.
    """
    def __init__(self, config: Dict[str, Any]):
        super().__init__(config, model_name='gemini-2.0-flash')

    def _ping_api(self):
        """Проверяет доступность API Gemini при инициализации."""
        try:
            timeout = self.config.get('ai_ping_timeout', 10)
            logger.debug(f"Проверка доступности API Gemini с таймаутом {timeout}с...")
            self.model.generate_content("ping", request_options={'timeout': timeout})
        except Exception as e:
            raise ConnectionError(f"Не удалось подключиться к API Gemini: {e}") from e

    # ### УЛУЧШЕНИЕ: Добавляем параметр generation_config ###
    async def _get_response_with_cache(self, prompt: str, context: str, use_cache: bool = True, generation_config: Dict[str, Any] = None) -> str:
        """Переопределяем метод для более строгой обработки ошибок и гибкой конфигурации."""
        prompt_hash = hashlib.md5(prompt.encode('utf-8')).hexdigest()
        if use_cache and (cached_response := self.cache.get(prompt_hash)):
            response_text, timestamp = cached_response
            if time.time() - timestamp < self.config.get('ai_cache_ttl', 3600):
                logger.info(f"Использование кэшированного ответа для '{context}'.")
                return response_text

        logger.debug(f"Отправка нового запроса в ИИ. Контекст: {context}")
        
        # Устанавливаем конфигурацию генерации
        gen_config = genai.types.GenerationConfig(**(generation_config or {}))
        
        response = await self.model.generate_content_async(prompt, generation_config=gen_config)
        
        if not response.parts:
            logger.warning(f"Ответ от ИИ был заблокирован. Фидбек: {response.prompt_feedback}")
            raise ContentBlockedError("Ответ от ИИ был заблокирован из-за настроек безопасности.", prompt_feedback=response.prompt_feedback)

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

    # ### УЛУЧШЕНИЕ: Более сфокусированный и чистый промпт ###
    def _create_plan_prompt(self, system_data: Dict, profiles: List[str], kb: Dict) -> str:
        """Создает промпт для генерации плана оптимизации."""
        # Убираем лишние данные, чтобы сфокусировать ИИ на главном
        relevant_kb_rules = kb.get('optimization_rules', [])
        
        return f"""
        You are an expert Windows optimization engineer. Your task is to create a safe and effective optimization plan in a single, valid JSON object with two keys: "action_plan" and "cleanup_plan".

        **1. Analyze System Components (for "action_plan"):**
        - Review the `system_components` data.
        - Based on the user profiles {json.dumps(profiles)}, identify non-essential services and UWP apps.
        - Use the provided `KNOWLEDGE_BASE` to check for safety. NEVER suggest actions on items marked as 'critical'.
        - Do not suggest actions on items relevant to the user's profiles.
        - For each valid action, create an object for the "action_plan" list with keys: "type", "id", "action", "reason", "user_explanation_ru".

        **2. Analyze Junk Files (for "cleanup_plan"):**
        - Review the `junk_files_report`. Each key is a category to be cleaned.
        - For EVERY category, create an entry in the "cleanup_plan" dictionary.
        - Set the value to `{{"clean": true}}` if you are confident it is safe to clean for this user.
        - Set it to `{{"clean": false}}` if it's risky (e.g., cleaning `python_pip_cache` for a 'Developer').

        **USER PROFILES:** {json.dumps(profiles)}

        **KNOWLEDGE BASE (Safety Rules):**
        {json.dumps(relevant_kb_rules, indent=2)}

        **SYSTEM SNAPSHOT (Data to Analyze):**
        {json.dumps(system_data, indent=2, default=str)}

        Respond with ONLY the JSON object.
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

    async def generate_distillation_plan(self, system_data: Dict, profiles: List[str], kb: Dict) -> Dict:
        """Генерирует и валидирует план оптимизации с помощью внутреннего валидатора."""
        prompt = self._create_plan_prompt(system_data, profiles, kb)
        
        # ### УЛУЧШЕНИЕ: Используем строгую конфигурацию для получения JSON ###
        generation_config = {
            "temperature": 0.1,
            "max_output_tokens": 8192,
        }
        
        try:
            response_text = await self._get_response_with_cache(
                prompt, "generate_distillation_plan", 
                use_cache=False, 
                generation_config=generation_config
            )
            plan = AICommunicator._extract_json_from_response(response_text)
            
            validator = _PlanValidator(full_kb=kb, user_profiles=profiles)
            safe_plan = validator.validate(plan)
            
            logger.debug("Получен и валидирован безопасный план от ИИ.")
            return safe_plan
        except ValueError as e:
            logger.error(f"Не удалось распарсить или валидировать план от ИИ: {e}", exc_info=True)
            raise RuntimeError("Не удалось получить корректный план от ИИ. Ответ был поврежден или невалиден.") from e
        except ContentBlockedError as e:
            logger.error(f"Не удалось сгенерировать план: {e}", exc_info=True)
            raise RuntimeError("Не удалось сгенерировать план: ответ от ИИ был заблокирован.") from e

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