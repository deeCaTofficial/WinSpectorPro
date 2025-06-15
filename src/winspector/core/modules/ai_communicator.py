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

# Наследуемся от базового класса с общей логикой
from .ai_base import AIBase

logger = logging.getLogger(__name__)


class AICommunicator(AIBase):
    """
    Использует ИИ для взаимодействия с пользователем и разработчиком.
    Отвечает за задачи, не связанные с генерацией основного плана оптимизации.
    """
    def __init__(self, config: Dict[str, Any]):
        # Можно использовать другую, более быструю модель для коммуникаций, если нужно
        super().__init__(config, model_name='gemini-2.0-flash')

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
        Analyze the user's system data to determine their primary profile.
        Available profiles: 'Gamer', 'Developer', 'Designer', 'OfficeWorker', 'HomeUser'.
        Base your decision on hardware specs, installed software keywords, and filesystem markers.
        Respond with ONLY ONE word in a JSON object: {{"profile": "..."}}.
        
        Profiler Configuration (keywords to look for):
        {json.dumps(kb_config, indent=2)}

        System Data:
        {json.dumps(system_data, indent=2, default=str)}
        """

    def _create_report_prompt(self, summary: Dict, plan: List[Dict]) -> str:
        """Создает промпт для генерации финального отчета."""
        def format_bytes(b: int) -> str:
            if b <= 0: return "0 байт"
            gb = b / (1024**3)
            if gb >= 1: return f"{gb:.2f} ГБ"
            mb = b / (1024**2)
            if mb >= 1: return f"{mb:.1f} МБ"
            kb = b / 1024
            if kb >= 1: return f"{kb:.1f} КБ"
            return f"{b} байт"
            
        debloat_summary = summary.get("debloat", {})
        cleanup_summary = summary.get("cleanup", {})
        
        cleaned_size = format_bytes(cleanup_summary.get("cleaned_size_bytes", 0))
        disabled_services = len(debloat_summary.get("disabled_services", []))
        removed_apps = len(debloat_summary.get("removed_apps", []))
        
        actions_performed_str = "\n".join(
            [f"✅ {action['user_explanation_ru']}" for action in plan if action.get("user_explanation_ru")]
        )
        if not actions_performed_str:
            actions_performed_str = "Действий по оптимизации компонентов не потребовалось."

        return f"""
        You are "WinSpector AI Communicator". Your job is to create a friendly, encouraging report in Russian Markdown.
        The tone should be positive and celebratory. Use simple, clear language and emojis (✅, 🚀, 💪).
        
        METRICS:
        - Space freed: {cleaned_size}
        - Services disabled: {disabled_services}
        - UWP apps removed: {removed_apps}
        
        ACTIONS PERFORMED:
        {actions_performed_str}
        
        TASK:
        Create a concise, well-formatted report in Russian Markdown. It must include:
        1. An encouraging headline.
        2. A summary of key metrics.
        3. A "Что было сделано:" section with the list of actions.
        4. A reassuring closing statement about safety.
        """

    # --- Публичные методы API ---

    async def determine_user_profile(self, system_data: Dict, kb_config: Dict) -> str:
        """Определяет профиль пользователя на основе системных данных."""
        logger.info("Запрос к ИИ для определения профиля пользователя.")
        prompt = self._create_profile_prompt(system_data, kb_config)
        response_text = await self._get_response_with_cache(prompt, "determine_user_profile")
        
        try:
            profile_data = self._extract_json_from_response(response_text)
            profile = profile_data.get("profile", "HomeUser").strip().replace('"', '')
            if not profile: return "HomeUser" # Если профиль пустой
            logger.info(f"ИИ определил профиль как: {profile}")
            return profile
        except (json.JSONDecodeError, ValueError) as e:
            logger.error(f"Не удалось определить профиль пользователя из ответа ИИ: {e}")
            return "HomeUser"  # Возвращаем безопасный профиль по умолчанию

    async def generate_final_report(self, summary: Dict, plan: List[Dict]) -> str:
        """Генерирует дружелюбный отчет для пользователя в формате Markdown."""
        logger.info("Генерация финального отчета.")
        prompt = self._create_report_prompt(summary, plan)
        # Отчет всегда должен быть свежим, не используем кеш
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