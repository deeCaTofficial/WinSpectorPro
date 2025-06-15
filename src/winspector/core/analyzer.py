# src/winspector/core/analyzer.py
"""
Главный класс-оркестратор. Управляет всеми модулями для выполнения
комплексной автономной оптимизации под контролем ИИ.
"""
import asyncio
import logging
import yaml
from typing import Callable, Dict, Any

# Импортируем все модули анализа из пакета `modules`
from .modules import (
    AIAnalyzer,
    AICommunicator,
    SmartCleaner,
    UserProfiler,
    WindowsOptimizer,
)

logger = logging.getLogger(__name__)


class WinSpectorCore:
    """
    Центральное ядро, управляющее всеми модулями анализа и оптимизации.
    Выступает в роли "Фасада" для GUI.
    """

    def __init__(self, config: Dict[str, Any]):
        """
        Инициализирует ядро и все его аналитические модули.

        Args:
            config: Словарь с конфигурацией приложения, содержащий пути.
        
        Raises:
            RuntimeError: Если не удалось загрузить или распарсить knowledge_base.yaml.
        """
        logger.info("Инициализация ядра WinSpectorCore (Advanced)...")
        self.config = config
        
        try:
            self.knowledge_base = self._load_knowledge_base()
            logger.info("База знаний успешно загружена.")
        except (FileNotFoundError, yaml.YAMLError) as e:
            logger.critical(f"Критическая ошибка: не удалось загрузить базу знаний. {e}", exc_info=True)
            raise RuntimeError(f"Не удалось загрузить или прочитать knowledge_base.yaml: {e}") from e

        # Инициализируем модули, передавая им только необходимые части конфигурации
        self.user_profiler = UserProfiler(self.knowledge_base.get('user_profiler_config', {}))
        self.windows_optimizer = WindowsOptimizer()
        self.smart_cleaner = SmartCleaner(self.knowledge_base.get('heuristic_rules', {}))
        
        # Инициализируем два специализированных AI-модуля
        self.ai_analyzer = AIAnalyzer(config)
        self.ai_communicator = AICommunicator(config)

        self.background_tasks = set()  # <--- ИЗМЕНЕНИЕ: Сет для отслеживания фоновых задач

        logger.info("Все модули ядра успешно инициализированы.")

    def _load_knowledge_base(self) -> Dict[str, Any]:
        """Загружает базу знаний из YAML файла и валидирует ее."""
        kb_path = self.config.get('kb_path')
        if not kb_path or not kb_path.exists():
            raise FileNotFoundError(f"Файл базы знаний не найден по пути: {kb_path}")
            
        with open(kb_path, 'r', encoding='utf-8') as f:
            knowledge_base = yaml.safe_load(f)
            if not isinstance(knowledge_base, dict):
                raise yaml.YAMLError("Корень knowledge_base.yaml должен быть словарем (mapping).")
            return knowledge_base

    async def _run_ai_self_reflection(self, **kwargs) -> None:
        """Безопасно запускает фоновую задачу саморефлексии ИИ."""
        try:
            # Используем AICommunicator для этой задачи
            suggestions = await self.ai_communicator.get_ai_suggestions_for_improvement(**kwargs)
            logger.info(f"\n--- ПРЕДЛОЖЕНИЯ ОТ ИИ ДЛЯ РАЗРАБОТЧИКОВ ---\n{suggestions}\n-----------------------------------------")
        except Exception as e:
            logger.warning(f"Не удалось получить предложения по улучшению от ИИ: {e}", exc_info=True)

    async def run_autonomous_optimization(
        self,
        is_cancelled: Callable[[], bool],
        progress_callback: Callable[[int, str], None]
    ) -> str:
        """
        Выполняет полный цикл автономной оптимизации с обратной связью и возможностью отмены.
        """
        logger.info("--- НАЧАЛО СЦЕНАРИЯ АВТОНОМНОЙ ОПТИМИЗАЦИИ ---")
        
        try:
            # --- Этап 1: Подготовка и профилирование ---
            progress_callback(5, "Создание точки восстановления...")
            
            # Запускаем блокирующую функцию создания точки восстановления в отдельном потоке,
            # чтобы не заморозить асинхронный цикл.
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                None, # Используем стандартный ThreadPoolExecutor
                self.windows_optimizer.create_restore_point
            )
            
            logger.info("Точка восстановления успешно создана.")
            progress_callback(10, "Точка восстановления создана.")

            if is_cancelled(): return "Операция отменена."
            
            progress_callback(15, "Анализ вашего стиля работы...")
            # get_system_profile содержит блокирующие вызовы WMI, выполняем в потоке
            # БЫЛО НЕПРАВИЛЬНО:
            # system_profile_data = await asyncio.to_thread(self.user_profiler.get_system_profile)

            # СТАЛО ПРАВИЛЬНО:
            system_profile_data = await self.user_profiler.get_system_profile()
            
            if is_cancelled(): return "Операция отменена."

            # Дальнейший код остается без изменений
            user_profile = await self.ai_communicator.determine_user_profile(
                system_profile_data,
                self.knowledge_base.get('user_profiler_config', {})
            )
            logger.info(f"ИИ определил профиль пользователя как '{user_profile}'.")
            progress_callback(25, f"Обнаружен профиль: '{user_profile}'. Готовимся к глубокому анализу.")

            # --- Этап 2: Глубокий сбор данных ---
            progress_callback(30, "Сбор данных о компонентах системы...")
            
            # get_system_components и find_junk_files_deep - это корутины, 
            # их нужно запускать как задачи, а не через to_thread.
            components_task = asyncio.create_task(self.windows_optimizer.get_system_components())
            junk_files_task = asyncio.create_task(self.smart_cleaner.find_junk_files_deep())
            
            system_components, junk_files_report = await asyncio.gather(
                components_task, junk_files_task
            )
            
            logger.info(f"Сбор данных завершен. Найдено служб: {len(system_components.get('services', []))}, "
                        f"UWP-приложений: {len(system_components.get('uwp_apps', []))}, "
                        f"категорий мусора: {len(junk_files_report)}.")

            # --- Этап 3: AI-анализ и генерация плана ---
            if is_cancelled(): return "Операция отменена."
            progress_callback(55, "ИИ создает персональный план оптимизации...")
            
            comprehensive_data = { "system_components": system_components, "junk_files_report": junk_files_report }
            # Используем AIAnalyzer для генерации плана
            plan = await self.ai_analyzer.generate_distillation_plan(
                comprehensive_data, user_profile, self.knowledge_base
            )
            
            action_plan = plan.get("action_plan", [])
            cleanup_plan = plan.get("cleanup_plan", {})
            
            # --- Этап 4: Исполнение плана ---
            if is_cancelled(): return "Операция отменена."

            # Если есть что выполнять, показываем прогресс
            if action_plan or any(v.get("clean") for v in cleanup_plan.values()):
                progress_callback(70, "Применение безопасных оптимизаций...")
                debloat_task = self.windows_optimizer.execute_action_plan(action_plan, progress_callback)
                cleanup_task = self.smart_cleaner.perform_deep_cleanup(cleanup_plan)
                debloat_summary, cleanup_summary = await asyncio.gather(debloat_task, cleanup_task)
            else:
                # Если план пустой, создаем пустые отчеты
                logger.info("ИИ не нашел ничего для оптимизации. Пропускаем этап выполнения.")
                debloat_summary = {"disabled_services": [], "removed_apps": [], "errors": []}
                cleanup_summary = {"cleaned_size_bytes": 0, "deleted_files_count": 0, "errors": 0}

            # --- Этап 5: Финальный отчет (теперь выполняется всегда) ---
            progress_callback(95, "Формирование отчета...")
            final_summary = {"debloat": debloat_summary, "cleanup": cleanup_summary}
            # Используем AICommunicator для генерации отчета
            final_report = await self.ai_communicator.generate_final_report(final_summary, action_plan)

            # --- Этап 6: Саморефлексия ИИ (фоновая задача) ---
            logger.info("Запуск фоновой задачи саморефлексии ИИ...")
            reflection_args = {
                "user_profile": user_profile,
                "system_data": comprehensive_data,
                "plan": plan,
                "summary": final_summary
            }
            # <--- ИЗМЕНЕНИЕ: Создаем задачу, добавляем ее в сет и устанавливаем callback для удаления
            task = asyncio.create_task(self._run_ai_self_reflection(**reflection_args))
            self.background_tasks.add(task)
            task.add_done_callback(self.background_tasks.discard)

            progress_callback(100, "Готово!")
            logger.info("--- СЦЕНАРИЙ АВТОНОМНОЙ ОПТИМИЗАЦИИ УСПЕШНО ЗАВЕРШЕН ---")
            return final_report

        except Exception as e:
            logger.critical(f"Критическая ошибка в сценарии оптимизации: {e}", exc_info=True)
            raise

    # <--- ИЗМЕНЕНИЕ: Добавлен новый метод для грациозного завершения
    async def shutdown(self, **kwargs): # <--- ИЗМЕНЕНИЕ
        """
        Грациозно завершает работу ядра, дожидаясь выполнения фоновых задач.
        Принимает и игнорирует любые ключевые аргументы для совместимости.
        """
        if not self.background_tasks:
            logger.info("Нет активных фоновых задач для завершения.")
            return

        logger.info(f"Ожидание завершения {len(self.background_tasks)} фоновых задач...")
        tasks_to_wait = list(self.background_tasks)
        try:
            await asyncio.gather(*tasks_to_wait, return_exceptions=True)
            logger.info("Все фоновые задачи успешно завершены.")
        except Exception as e:
            logger.error(f"Произошла ошибка при ожидании завершения фоновых задач: {e}", exc_info=True)