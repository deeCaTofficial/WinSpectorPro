# src/winspector/core/modules/ai_communicator.py
"""
Модуль, отвечающий за "коммуникационные" задачи ИИ:
- Определение профиля пользователя.
- Генерация отчетов для пользователя.
- Генерация предложений по улучшению для разработчиков.
"""
import json
import logging
import re
from typing import Dict, Any, List

from .ai_base import AIBase

logger = logging.getLogger(__name__)


class AICommunicator(AIBase):
    """
    Использует ИИ для взаимодействия с пользователем и разработчиком.
    Отвечает за задачи, не связанные с генерацией основного плана оптимизации.
    """
    def __init__(self, config: Dict[str, Any]):
        super().__init__(config, model_name='gemini-2.0-flash')

    @staticmethod
    def _extract_json_from_response(text: str) -> Dict:
        """Надежно извлекает JSON объект из текстового ответа ИИ, удаляя обертку ```json."""
        match = re.search(r'```json\s*(\{.*\}|\[.*\])\s*```', text, re.DOTALL)
        
        json_text = match.group(1) if match else text

        try:
            return json.loads(json_text)
        except json.JSONDecodeError as e:
            # ### УЛУЧШЕНИЕ: Попытка восстановить JSON ###
            logger.warning(f"Получен невалидный JSON. Ошибка: {e}. Пытаемся восстановить...")
            # Простая эвристика: ищем последний корректный объект или список
            # Это полезно, если ответ был просто оборван.
            corrected_text = re.sub(r'[,\s]+$', '', json_text) # Убираем висячие запятые
            if corrected_text.count('{') > corrected_text.count('}'):
                 corrected_text += '}'
            if corrected_text.count('[') > corrected_text.count(']'):
                 corrected_text += ']'
            
            try:
                logger.info("Попытка парсинга восстановленного JSON.")
                return json.loads(corrected_text)
            except json.JSONDecodeError:
                 logger.error(f"Не удалось восстановить JSON. Исходный текст: {json_text}")
                 raise ValueError(f"JSON-объект не найден или некорректен в ответе ИИ.") from e

    # --- Методы для генерации промптов ---

    def _create_profile_prompt(self, system_data: Dict, kb_config: Dict) -> str:
        # ### УЛУЧШЕНИЕ: Добавляем больше контекста для определения профиля ###
        return f"""
        Analyze the user's system data to determine their profiles. A user can have multiple profiles.
        Available profiles: 'Gamer', 'Developer', 'Designer', 'OfficeWorker', 'Streamer', 'ContentCreator', 'AudioEngineer', 'PowerUser', 'HomeUser'.
        
        Your task is to return a JSON object with a key "profiles" containing a LIST of all relevant profile strings.
        - If no specific profile is detected, return ["HomeUser"].
        - Use `user_folder_stats` and `shortcuts` as strong indicators of user activity.
        
        Example for a developer who also plays games:
        {{
          "profiles": ["Developer", "Gamer", "PowerUser"]
        }}
        
        Profiler Configuration (keywords to look for in software list):
        {json.dumps(kb_config, indent=2)}

        System Data (Pay close attention to `shortcuts` and `user_folder_stats`):
        {json.dumps(system_data, indent=2, default=str)}
        """

    def _create_report_prompt(self, summary: Dict, plan: List[Dict], profiles: List[str]) -> str:
        """Создает промпт для генерации финального отчета с динамической тональностью и учетом профиля."""
        
        debloat_summary = summary.get("debloat", {})
        cleanup_summary = summary.get("cleanup", {})
        empty_folders_summary = summary.get("empty_folders", {})
        disabled_services_count = len(debloat_summary.get("disabled_services", []))
        removed_apps_count = len(debloat_summary.get("removed_apps", []))
        cleaned_size_bytes = cleanup_summary.get("cleaned_size_bytes", 0)
        deleted_folders_count = empty_folders_summary.get("deleted_folders_count", 0)
        total_actions = disabled_services_count + removed_apps_count

        def format_bytes(b: int) -> str:
            if b <= 0: return "0 байт"
            gb, mb, kb = b / (1024**3), b / (1024**2), b / 1024
            return f"{gb:.2f} ГБ" if gb >= 1 else f"{mb:.1f} МБ" if mb >= 1 else f"{kb:.1f} КБ" if kb >= 1 else f"{b} байт"

        if total_actions > 5 or cleaned_size_bytes > 500 * 1024 * 1024:
            tone_instruction = "The tone should be positive and celebratory. Use emojis like ✅, 🚀, 💪."
            headline_suggestion = "## 🚀 Отличная работа! Ваша система оптимизирована."
        elif total_actions > 0 or cleaned_size_bytes > 0:
            tone_instruction = "The tone should be calm and informative, like a helpful assistant."
            headline_suggestion = "## ✅ Оптимизация завершена."
        else:
            tone_instruction = "The tone should be reassuring and professional. Explain that the system is already in good shape."
            headline_suggestion = "## 🛡️ Ваша система в прекрасном состоянии!"

        actions_performed_str = "\n".join(
            [f"- {action['user_explanation_ru']}" for action in plan if action.get("user_explanation_ru")]
        )
        if not actions_performed_str:
            actions_performed_str = "Действий по оптимизации компонентов не потребовалось."

        # ### УЛУЧШЕНИЕ: Добавляем профили в промпт ###
        return f"""
        You are "WinSpector AI Communicator". Your job is to create a friendly, well-formatted report in Russian Markdown.

        **USER PROFILES:** {json.dumps(profiles)}
        Use this information to add context to your report. For example: "Since you are a Gamer, we focused on..."

        **TONE & HEADLINE:**
        - Tone: {tone_instruction}
        - Suggested Headline: {headline_suggestion}

        **METRICS TO INCLUDE:**
        - Space freed: {format_bytes(cleaned_size_bytes)}
        - Services disabled: {disabled_services_count}
        - UWP apps removed: {removed_apps_count}
        - Empty folders removed: {deleted_folders_count}

        **DETAILED ACTIONS PERFORMED:**
        {actions_performed_str}

        **TASK:**
        Create a concise, personalized report in Russian Markdown. It must include:
        1. A headline reflecting the outcome.
        2. A summary of key metrics.
        3. A "Что было сделано:" section with the list of actions.
        4. A personalized closing statement about safety, mentioning the user's profiles if relevant.
        """

    # --- Публичные методы API ---

    async def determine_user_profile(self, system_data: Dict, kb_config: Dict) -> List[str]:
        """Определяет набор профилей пользователя на основе системных данных."""
        logger.info("Запрос к ИИ для определения набора профилей пользователя.")
        prompt = self._create_profile_prompt(system_data, kb_config)
        response_text = await self._get_response_with_cache(prompt, "determine_user_profile")
        
        try:
            profile_data = self._extract_json_from_response(response_text)
            profiles = profile_data.get("profiles", ["HomeUser"])
            
            if not isinstance(profiles, list) or not all(isinstance(p, str) for p in profiles) or not profiles:
                logger.warning(f"ИИ вернул некорректный формат для профилей: {profiles}. Используется 'HomeUser'.")
                return ["HomeUser"]

            logger.info(f"ИИ определил профили: {profiles}")
            return profiles
        except (json.JSONDecodeError, ValueError) as e:
            logger.error(f"Не удалось определить профиль пользователя из ответа ИИ: {e}")
            return ["HomeUser"]

    # ### УЛУЧШЕНИЕ: Метод теперь принимает профили для контекста ###
    async def generate_final_report(self, summary: Dict, plan: List[Dict], profiles: List[str]) -> str:
        """Генерирует дружелюбный и контекстно-зависимый отчет для пользователя."""
        logger.info("Генерация финального отчета...")
        prompt = self._create_report_prompt(summary, plan, profiles)
        return await self._get_response_with_cache(prompt, "generate_final_report", use_cache=False)

    async def get_ai_suggestions_for_improvement(self, **kwargs) -> str:
        """Анализирует сессию и предлагает улучшения для разработчиков."""
        # Этот метод может оставаться таким же, так как его промпт очень специфичен
        # и сложен для формализации в отдельном методе.
        logger.info("Запрос к ИИ на саморефлексию и предложения по улучшению.")
        
        prompt = f"""
        You are "WinSpector AI Architect", a lead developer reviewing an optimization session.
        Your goal is to suggest future improvements for the application.
        
        SESSION ANALYSIS (JSON format):
        {json.dumps(kwargs, indent=2, ensure_ascii=False, default=str)}
        
        TASK:
        Based on this session's data, suggest 3-5 concrete, technical improvements for future versions.
        Focus on proactive monitoring, security, and commercial readiness.
        Respond in Russian Markdown.
        """
        # Этот запрос тоже всегда должен быть свежим
        response = await self.model.generate_content_async(prompt)
        return response.text