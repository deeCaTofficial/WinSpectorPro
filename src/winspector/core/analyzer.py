# src/winspector/core/analyzer.py
"""
Главный класс-оркестратор. Управляет всеми модулями для выполнения
комплексной автономной оптимизации под контролем ИИ.
Финальная версия с оптимизированным потоком выполнения.
"""
import asyncio
import logging
import yaml
from pathlib import Path
from typing import Callable, Dict, Any, List, TypedDict, Optional
from datetime import datetime, timedelta

from .modules import (
    AIAnalyzer,
    AICommunicator,
    SmartCleaner,
    UserProfiler,
    WindowsOptimizer,
)

logger = logging.getLogger(__name__)

class OptimizationSessionData(TypedDict, total=False):
    """Контейнер для данных, собираемых и генерируемых в ходе сессии."""
    system_profile: Dict[str, Any]
    user_profile: List[str]
    system_components: Dict[str, List[Dict]]
    junk_files_report: Dict[str, Any]
    comprehensive_data: Dict[str, Any]
    ai_plan: Dict[str, Any]
    debloat_summary: Dict[str, Any]
    standard_cleanup_summary: Dict[str, Any]
    ai_cleanup_summary: Dict[str, Any]
    empty_folders_summary: Dict[str, Any]
    final_summary: Dict[str, Any]
    final_report: str


class WinSpectorCore:
    """
    Центральное ядро, управляющее всеми модулями анализа и оптимизации.
    """
    def __init__(self, config: Dict[str, Any]):
        logger.info("Инициализация ядра WinSpectorCore (Advanced)...")
        self.config = config
        self._last_scan_time: Optional[datetime] = None
        self._cached_system_components: Optional[Dict[str, Any]] = None
        self.CACHE_TTL_MINUTES = 5
        try:
            self.knowledge_base = self._load_knowledge_base()
            logger.info("База знаний успешно загружена и объединена.")
        except (FileNotFoundError, yaml.YAMLError) as e:
            logger.critical(f"Критическая ошибка: не удалось загрузить базу знаний. {e}", exc_info=True)
            raise RuntimeError(f"Не удалось загрузить или прочитать файлы базы знаний: {e}") from e
        self.user_profiler = UserProfiler()
        self.windows_optimizer = WindowsOptimizer(optimization_rules=self.knowledge_base.get('optimization_rules', []))
        self.smart_cleaner = SmartCleaner(cleanup_rules=self.knowledge_base.get('cleanup_rules', []))
        self.ai_analyzer = AIAnalyzer(config)
        self.ai_communicator = AICommunicator(config)
        self.background_tasks = set()
        logger.info("Все модули ядра успешно инициализированы.")

    def _load_knowledge_base(self) -> Dict[str, Any]:
        kb_dir_path = self.config.get('kb_path')
        if not kb_dir_path or not kb_dir_path.is_dir():
            raise FileNotFoundError(f"Директория базы знаний не найдена по пути: {kb_dir_path}")
        combined_kb: Dict[str, Any] = {}
        for yaml_file in kb_dir_path.glob("*.yaml"):
            key_name = yaml_file.stem
            with open(yaml_file, 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f)
                if not isinstance(data, (dict, list)):
                    raise yaml.YAMLError(f"Файл {yaml_file.name} должен содержать список или словарь.")
                combined_kb[key_name] = data
        if not combined_kb:
            raise FileNotFoundError(f"В директории {kb_dir_path} не найдено ни одного .yaml файла.")
        return combined_kb

    async def _run_ai_self_reflection(self, session_data: OptimizationSessionData) -> None:
        logger.info("Запуск фоновой задачи саморефлексии ИИ...")
        reflection_args = {
            "user_profile": session_data.get("user_profile"),
            "system_data": session_data.get("comprehensive_data"),
            "plan": session_data.get("ai_plan"),
            "summary": session_data.get("final_summary")
        }
        try:
            suggestions = await self.ai_communicator.get_ai_suggestions_for_improvement(**reflection_args)
            logger.info(f"\n--- ПРЕДЛОЖЕНИЯ ОТ ИИ ДЛЯ РАЗРАБОТЧИКОВ ---\n{suggestions}\n-----------------------------------------")
        except Exception as e:
            logger.warning(f"Не удалось получить предложения по улучшению от ИИ: {e}", exc_info=True)

    async def run_autonomous_optimization(
        self,
        is_cancelled: Callable[[], bool],
        progress_callback: Callable[[int, str], None]
    ) -> str:
        logger.info("--- НАЧАЛО СЦЕНАРИЯ АВТОНОМНОЙ ОПТИМИЗАЦИИ ---")
        session = OptimizationSessionData()
        try:
            await self._step_create_restore_point(progress_callback);       _check_cancellation(is_cancelled)
            await self._step_profile_user(session, progress_callback);          _check_cancellation(is_cancelled)
            await self._step_collect_data_for_ai(session, progress_callback);   _check_cancellation(is_cancelled)
            await self._step_generate_ai_plan(session, progress_callback);      _check_cancellation(is_cancelled)
            
            progress_callback(70, "Применение оптимизаций и очистка системы...")
            cleanup_task = self._step_execute_full_cleanup(session)
            action_task = self._step_execute_action_plan(session, progress_callback)
            await asyncio.gather(cleanup_task, action_task)
            _check_cancellation(is_cancelled)
            
            await self._step_generate_final_report(session, progress_callback)
            
            task = asyncio.create_task(self._run_ai_self_reflection(session))
            self.background_tasks.add(task)
            task.add_done_callback(self.background_tasks.discard)

            logger.info("--- СЦЕНАРИЙ АВТОНОМНОЙ ОПТИМИЗАЦИИ УСПЕШНО ЗАВЕРШЕН ---")
            return session.get("final_report", "Оптимизация завершена. Отчет не был создан.")
        except asyncio.CancelledError:
            logger.info("Сценарий оптимизации отменен пользователем.")
            return "Отменено пользователем."
        except Exception as e:
            logger.critical(f"Критическая ошибка в сценарии оптимизации: {e}", exc_info=True)
            raise

    async def _step_create_restore_point(self, progress_callback: Callable[[int, str], None]):
        progress_callback(5, "Создание точки восстановления...")
        try:
            await asyncio.to_thread(self.windows_optimizer.create_restore_point)
            logger.info("Точка восстановления успешно создана.")
            progress_callback(10, "Точка восстановления создана.")
        except Exception as e:
            logger.error(f"Не удалось создать точку восстановления: {e}", exc_info=True)
            raise RuntimeError(f"Не удалось создать точку восстановления. {e}")

    async def _step_profile_user(self, session: OptimizationSessionData, progress_callback: Callable[[int, str], None]):
        progress_callback(15, "Анализ вашего стиля работы...")
        session['system_profile'] = await self.user_profiler.get_system_profile()
        
        user_profiler_config = self.knowledge_base.get('user_profiler_config', {})
        session['user_profile'] = await self.ai_communicator.determine_user_profile(
            session['system_profile'], user_profiler_config
        )
        profiles_str = ", ".join(session['user_profile'])
        logger.info(f"ИИ определил профили пользователя: {profiles_str}")
        progress_callback(25, f"Обнаружены профили: {profiles_str}.")

    async def _step_standard_cleanup(self, session: OptimizationSessionData, progress_callback: Callable[[int, str], None]):
        progress_callback(30, "Выполнение стандартной очистки...")
        session['standard_cleanup_summary'] = await self.smart_cleaner.perform_standard_cleanup()
        logger.info("Стандартная очистка завершена.")

    async def _step_collect_data_for_ai(self, session: OptimizationSessionData, progress_callback: Callable[[int, str], None]):
        progress_callback(40, "Сбор данных для ИИ-анализа...")
        if self._cached_system_components and self._last_scan_time and \
           (datetime.now() - self._last_scan_time) < timedelta(minutes=self.CACHE_TTL_MINUTES):
            logger.info("Использование кэшированных данных о компонентах системы.")
            components_task = asyncio.create_task(asyncio.sleep(0, result=self._cached_system_components))
        else:
            logger.info("Кэш устарел или отсутствует. Запуск полного сканирования компонентов.")
            components_task = self.windows_optimizer.get_system_components()
            self._last_scan_time = datetime.now()
        junk_files_task = self.smart_cleaner.find_junk_files_deep()
        components, junk_files = await asyncio.gather(components_task, junk_files_task)
        self._cached_system_components = components
        session['system_components'] = components
        session['junk_files_report'] = junk_files
        logger.info(f"Сбор данных для ИИ завершен.")

    async def _step_execute_full_cleanup(self, session: OptimizationSessionData):
        logger.info("Начало комплексной очистки системы...")
        standard_cleanup_task = self.smart_cleaner.perform_standard_cleanup()
        ai_cleanup_task = self.smart_cleaner.perform_deep_cleanup(session.get('ai_plan', {}).get("cleanup_plan", {}))
        std_summary, ai_summary = await asyncio.gather(standard_cleanup_task, ai_cleanup_task)
        session['standard_cleanup_summary'] = std_summary
        session['ai_cleanup_summary'] = ai_summary
        logger.info("Стандартная и интеллектуальная очистка завершены.")
        logger.info("Запуск очистки пустых директорий...")
        session['empty_folders_summary'] = await self.smart_cleaner.cleanup_all_empty_folders_async()
        logger.info(f"Удалено {session['empty_folders_summary']['deleted_folders_count']} пустых папок.")

    async def _step_execute_action_plan(self, session: OptimizationSessionData, progress_callback: Callable[[int, str], None]):
        logger.info("Применение оптимизаций системы...")
        action_plan = session.get('ai_plan', {}).get("action_plan", [])
        if not action_plan:
            logger.info("Действий по оптимизации компонентов не требуется.")
            session['debloat_summary'] = {"completed": [], "failed": []}
            return
        session['debloat_summary'] = await self.windows_optimizer.execute_action_plan(action_plan, progress_callback)
        logger.info("План оптимизации компонентов выполнен.")

    async def _step_generate_ai_plan(self, session: OptimizationSessionData, progress_callback: Callable[[int, str], None]):
        progress_callback(55, "ИИ создает персональный план оптимизации...")
        session['comprehensive_data'] = {
            "system_components": session['system_components'],
            "junk_files_report": session['junk_files_report']
        }
        
        user_profile = session['user_profile']
        relevant_kb = self._filter_kb_for_profile(user_profile)
        
        session['ai_plan'] = await self.ai_analyzer.generate_distillation_plan(
            session['comprehensive_data'], user_profile, relevant_kb
        )
        logger.info("План от ИИ успешно сгенерирован и валидирован.")

    def _filter_kb_for_profile(self, profiles: List[str]) -> Dict[str, Any]:
        """Фильтрует полную базу знаний, оставляя только релевантные для профиля правила."""
        filtered_kb = {}
        
        opt_rules = self.knowledge_base.get('optimization_rules', [])
        
        # ### ИЗМЕНЕНИЕ: Логика фильтрации для нескольких профилей ###
        filtered_kb['optimization_rules'] = [
            rule for rule in opt_rules
            # Правило подходит, если у него нет списка профилей (универсальное)
            if not rule.get('relevant_profiles') or
            # или если ХОТЯ БЫ ОДИН из профилей пользователя есть в списке правила
            any(p in rule.get('relevant_profiles', []) for p in profiles)
        ]
        
        filtered_kb['cleanup_rules'] = self.knowledge_base.get('cleanup_rules', [])
        filtered_kb['telemetry_domains'] = self.knowledge_base.get('telemetry_domains', [])
        
        logger.debug(f"База знаний отфильтрована для профилей {profiles}. "
                     f"Осталось {len(filtered_kb['optimization_rules'])} правил оптимизации.")
        return filtered_kb
        
    async def _step_cleanup_empty_folders(self, session: OptimizationSessionData, progress_callback: Callable[[int, str], None]):
        progress_callback(90, "Поиск и удаление пустых папок...")
        session['empty_folders_summary'] = await self.smart_cleaner.cleanup_all_empty_folders_async()
        logger.info(f"Удалено {session['empty_folders_summary']['deleted_folders_count']} пустых папок.")

    async def _step_generate_final_report(self, session: OptimizationSessionData, progress_callback: Callable[[int, str], None]):
        progress_callback(95, "Формирование отчета...")
        total_cleaned_bytes = (session.get('standard_cleanup_summary', {}).get('cleaned_size_bytes', 0) + 
                               session.get('ai_cleanup_summary', {}).get('cleaned_size_bytes', 0))
        session['final_summary'] = {
            "debloat": session.get('debloat_summary', {}),
            "cleanup": {"cleaned_size_bytes": total_cleaned_bytes},
            "empty_folders": session.get('empty_folders_summary', {})
        }
        action_plan = session.get('ai_plan', {}).get("action_plan", [])
        session['final_report'] = await self.ai_communicator.generate_final_report(
            session['final_summary'], action_plan, session['user_profile']
        )
        progress_callback(100, "Готово!")

    async def shutdown(self, **kwargs):
        if not self.background_tasks:
            logger.info("Нет активных фоновых задач для завершения.")
            return
        logger.info(f"Ожидание завершения {len(self.background_tasks)} фоновых задач...")
        await asyncio.gather(*self.background_tasks, return_exceptions=True)
        logger.info("Все фоновые задачи успешно завершены.")

def _check_cancellation(is_cancelled: Callable[[], bool]):
    """Вспомогательная функция для проверки отмены и выброса исключения."""
    if is_cancelled():
        raise asyncio.CancelledError