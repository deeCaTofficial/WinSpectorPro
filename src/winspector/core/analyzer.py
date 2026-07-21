# src/winspector/core/analyzer.py
"""
Оркестратор автономной оптимизации.

Порядок шагов подчинён одному правилу: ничего необратимого не происходит до
того, как создана точка восстановления и получен провалидированный план.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, TypedDict

import yaml

from . import offline_planner
from .config import DEFAULT_USER_PROFILER_CONFIG
from .exceptions import AIError, KnowledgeBaseError
from .modules import (
    AIAnalyzer,
    AIBase,
    AICommunicator,
    SmartCleaner,
    UserProfiler,
    WindowsOptimizer,
)
from .modules.plan_validator import PlanValidator, normalize_profiles
from .worker_pool import WorkerPool

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[int, str], None]

# Профиль по умолчанию, когда определить его нечем.
DEFAULT_PROFILE = "HomeUser"

_OFFLINE_HINT = (
    "\n\n---\n"
    "ℹ️ Оптимизация выполнена **без ИИ** — по встроенной базе знаний, "
    "поэтому изменения были подобраны осторожно и одинаково для всех.\n\n"
    "Чтобы получить персональный план с учётом того, как вы используете "
    "компьютер, укажите бесплатный ключ Gemini в настройках приложения."
)


class OptimizationSessionData(TypedDict, total=False):
    """Данные, накапливаемые в ходе одной сессии оптимизации."""

    system_profile: dict[str, Any]
    user_profile: list[str]
    system_components: dict[str, list[dict[str, Any]]]
    junk_files_report: dict[str, Any]
    comprehensive_data: dict[str, Any]
    ai_plan: dict[str, Any]
    debloat_summary: dict[str, Any]
    standard_cleanup_summary: dict[str, Any]
    ai_cleanup_summary: dict[str, Any]
    empty_folders_summary: dict[str, Any]
    final_summary: dict[str, Any]
    final_report: str
    ai_enabled: bool


class WinSpectorCore:
    """Центральное ядро: связывает модули анализа и оптимизации."""

    CACHE_TTL_MINUTES = 5
    # Сколько ждать фоновые задачи при остановке ядра.
    BACKGROUND_WAIT_SECONDS = 5

    def __init__(self, config: dict[str, Any]) -> None:
        logger.info("Инициализация ядра WinSpectorCore...")
        self.config = config

        # `force_offline` выставляется, когда пользователь сознательно выбрал
        # работу без ИИ. `ai_enabled` — фактический режим текущего запуска:
        # он может смениться на False прямо посреди сценария, если сервис
        # окажется недоступен.
        self.force_offline: bool = bool(config.get("force_offline", False))
        self.ai_enabled: bool = False

        self.knowledge_base = self._load_knowledge_base()
        logger.info(
            "База знаний загружена: %d правил оптимизации, %d правил очистки.",
            len(self.knowledge_base.get("optimization_rules", [])),
            len(self.knowledge_base.get("cleanup_rules", [])),
        )

        # Один пул процессов на всё ядро: WMI-воркеры переиспользуют его,
        # а закрывается он централизованно в `shutdown`.
        self.worker_pool = WorkerPool(max_workers=1)

        self.user_profiler = UserProfiler(worker_pool=self.worker_pool)
        self.windows_optimizer = WindowsOptimizer(
            optimization_rules=self.knowledge_base.get("optimization_rules", []),
            worker_pool=self.worker_pool,
        )
        self.smart_cleaner = SmartCleaner(
            cleanup_rules=self.knowledge_base.get("cleanup_rules", [])
        )
        self.ai_analyzer = AIAnalyzer(config)
        self.ai_communicator = AICommunicator(config)

        self._last_scan_time: datetime | None = None
        self._cached_system_components: dict[str, Any] | None = None
        self.background_tasks: set[asyncio.Task[Any]] = set()

        logger.info("Все модули ядра инициализированы.")

    # --- База знаний ------------------------------------------------------

    def _load_knowledge_base(self) -> dict[str, Any]:
        """Загружает и объединяет YAML-файлы базы знаний."""
        kb_path = self.config.get("kb_path")
        if not kb_path:
            raise KnowledgeBaseError("Путь к базе знаний не задан в конфигурации.")

        kb_dir = Path(kb_path)
        if not kb_dir.is_dir():
            raise KnowledgeBaseError(f"Директория базы знаний не найдена: {kb_dir}")

        combined: dict[str, Any] = {}
        for yaml_file in sorted(kb_dir.glob("*.yaml")):
            try:
                with yaml_file.open("r", encoding="utf-8") as handle:
                    data = yaml.safe_load(handle)
            except (OSError, yaml.YAMLError) as exc:
                raise KnowledgeBaseError(f"Не удалось прочитать '{yaml_file.name}': {exc}") from exc

            if data is None:
                logger.warning("Файл базы знаний '%s' пуст.", yaml_file.name)
                continue
            if not isinstance(data, (dict, list)):
                raise KnowledgeBaseError(
                    f"Файл '{yaml_file.name}' должен содержать список или словарь."
                )
            combined[yaml_file.stem] = data

        if not combined:
            raise KnowledgeBaseError(f"В '{kb_dir}' не найдено ни одного .yaml файла.")

        return combined

    def _profiler_config(self) -> dict[str, Any]:
        """Конфигурация профилировщика: из базы знаний либо резервная из кода."""
        kb_config = self.knowledge_base.get("user_profiler_config")
        if isinstance(kb_config, dict) and kb_config:
            return kb_config
        logger.debug("Секция 'user_profiler_config' отсутствует, используется резервная.")
        return DEFAULT_USER_PROFILER_CONFIG

    def _filter_kb_for_profile(self, profiles: list[str]) -> dict[str, Any]:
        """
        Отбирает правила, уместные для профилей пользователя.

        `relevant_profiles` означает «правило применимо к этим профилям».
        Правило без этого поля считается универсальным. Если под профиль не
        подошло ничего, отдаём весь набор: безопасность обеспечивает валидатор,
        а не сокращённый промпт, и пустая база знаний только ухудшает решения.
        """
        normalized = normalize_profiles(profiles)
        all_rules = self.knowledge_base.get("optimization_rules", []) or []

        selected = [
            rule
            for rule in all_rules
            if isinstance(rule, dict)
            and (
                not rule.get("relevant_profiles")
                or normalize_profiles(rule.get("relevant_profiles")) & normalized
            )
        ]

        if not selected and all_rules:
            logger.warning(
                "Под профили %s не подошло ни одного правила — используется полный набор.",
                sorted(normalized),
            )
            selected = [r for r in all_rules if isinstance(r, dict)]

        logger.debug(
            "Для профилей %s отобрано %d из %d правил оптимизации.",
            sorted(normalized),
            len(selected),
            len(all_rules),
        )
        return {
            "optimization_rules": selected,
            "cleanup_rules": self.knowledge_base.get("cleanup_rules", []),
        }

    # --- Основной сценарий ------------------------------------------------

    async def run_autonomous_optimization(
        self,
        is_cancelled: Callable[[], bool] | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> str:
        """
        Выполняет полный цикл оптимизации и возвращает отчёт в Markdown.

        Raises:
            asyncio.CancelledError: операция отменена пользователем.
            WinSpectorError: ошибка на одном из обязательных этапов.
        """
        logger.info("--- НАЧАЛО АВТОНОМНОЙ ОПТИМИЗАЦИИ ---")

        progress = progress_callback or (lambda value, text: None)
        check = _make_cancellation_check(is_cancelled)
        session: OptimizationSessionData = {}

        # Режим определяется до любых изменений в системе. Раньше отсутствие
        # ключа выяснялось только на этапе построения плана — уже после
        # создания точки восстановления и полного сканирования.
        self.ai_enabled = self._detect_ai_mode()
        session["ai_enabled"] = self.ai_enabled

        try:
            await self._step_create_restore_point(progress)
            check()

            await self._step_profile_user(session, progress)
            check()

            await self._step_collect_data(session, progress)
            check()

            await self._step_generate_ai_plan(session, progress)
            check()

            progress(70, "Применение оптимизаций и очистка системы...")
            await asyncio.gather(
                self._step_execute_cleanup(session),
                self._step_execute_action_plan(session, progress),
            )
            check()

            await self._step_generate_final_report(session, progress)
            self._spawn_background(self._run_ai_self_reflection(session))

            logger.info("--- ОПТИМИЗАЦИЯ УСПЕШНО ЗАВЕРШЕНА ---")
            return session.get("final_report") or "Оптимизация завершена."

        except asyncio.CancelledError:
            logger.info("Сценарий оптимизации отменён пользователем.")
            raise
        except Exception:
            logger.critical("Критическая ошибка сценария оптимизации.", exc_info=True)
            raise

    # --- Режим работы -----------------------------------------------------

    def _detect_ai_mode(self) -> bool:
        """
        Определяет, доступны ли ИИ-функции.

        Проверяется только наличие ключа — без обращения к сети. Если ключ
        задан, но окажется нерабочим, сценарий не прервётся: каждый шаг с ИИ
        умеет переключиться на офлайн-логику.
        """
        if self.force_offline:
            logger.info("Запрошен офлайн-режим: ИИ использоваться не будет.")
            return False
        if not AIBase.has_api_key():
            logger.warning("Ключ Gemini не настроен — работа в офлайн-режиме.")
            return False
        return True

    def _switch_to_offline(self, reason: str) -> None:
        """Переводит текущий запуск в офлайн-режим после сбоя ИИ."""
        if self.ai_enabled:
            logger.warning("ИИ недоступен (%s). Продолжаем без него.", reason)
            self.ai_enabled = False

    # --- Шаги -------------------------------------------------------------

    async def _step_create_restore_point(self, progress: ProgressCallback) -> None:
        progress(5, "Создание точки восстановления...")
        await self.windows_optimizer.create_restore_point()
        progress(10, "Точка восстановления создана.")

    async def _step_profile_user(
        self, session: OptimizationSessionData, progress: ProgressCallback
    ) -> None:
        progress(15, "Анализ вашего стиля работы...")
        session["system_profile"] = await self.user_profiler.get_system_profile()

        if not self.ai_enabled:
            # Без ИИ профиль не определить. `HomeUser` — самый осторожный
            # вариант: под него не подпадают послабления для «продвинутых».
            session["user_profile"] = [DEFAULT_PROFILE]
            progress(25, "Анализ системы завершён.")
            return

        session["user_profile"] = await self.ai_communicator.determine_user_profile(
            session["system_profile"], self._profiler_config()
        )
        progress(25, f"Обнаружены профили: {', '.join(session['user_profile'])}.")

    async def _step_collect_data(
        self, session: OptimizationSessionData, progress: ProgressCallback
    ) -> None:
        progress(40, "Сбор данных для ИИ-анализа...")

        components_task = (
            self._cached_components_task() or self.windows_optimizer.get_system_components()
        )
        components, junk_files = await asyncio.gather(
            components_task, self.smart_cleaner.find_junk_files_deep()
        )

        self._cached_system_components = components
        self._last_scan_time = datetime.now()
        session["system_components"] = components
        session["junk_files_report"] = junk_files

    def _cached_components_task(self) -> Any | None:
        """Возвращает готовый результат из кеша, если он ещё не устарел."""
        if not (self._cached_system_components and self._last_scan_time):
            return None
        if datetime.now() - self._last_scan_time >= timedelta(minutes=self.CACHE_TTL_MINUTES):
            return None
        logger.info("Используются кешированные данные о компонентах системы.")
        return asyncio.sleep(0, result=self._cached_system_components)

    async def _step_generate_ai_plan(
        self, session: OptimizationSessionData, progress: ProgressCallback
    ) -> None:
        session["comprehensive_data"] = {
            "system_components": session.get("system_components", {}),
            "junk_files_report": session.get("junk_files_report", {}),
        }

        if self.ai_enabled:
            progress(55, "ИИ создаёт персональный план оптимизации...")
            try:
                session["ai_plan"] = await self.ai_analyzer.generate_distillation_plan(
                    session["comprehensive_data"],
                    session.get("user_profile", []),
                    self._filter_kb_for_profile(session.get("user_profile", [])),
                )
                return
            except AIError as exc:
                # Точка восстановления уже создана, система просканирована —
                # прерывать работу здесь означало бы потратить время
                # пользователя впустую. Достраиваем план по базе знаний.
                self._switch_to_offline(str(exc))
                session["ai_enabled"] = False

        progress(55, "Составление плана по базе знаний...")
        session["ai_plan"] = self._build_offline_plan(session)

    def _build_offline_plan(self, session: OptimizationSessionData) -> dict[str, Any]:
        """Строит план без ИИ и пропускает его через тот же валидатор."""
        raw_plan = offline_planner.build_plan(
            self.knowledge_base,
            session.get("system_components"),
            session.get("junk_files_report"),
        )
        validator = PlanValidator(
            knowledge_base=self.knowledge_base,
            user_profiles=session.get("user_profile") or [DEFAULT_PROFILE],
            known_junk_categories=(session.get("junk_files_report") or {}).keys(),
        )
        return validator.validate(raw_plan)

    async def _step_execute_cleanup(self, session: OptimizationSessionData) -> None:
        """
        Очистка выполняется последовательно: стандартная и «умная» очистка
        пересекаются по %TEMP%, и параллельный запуск порождает гонки за одни
        и те же файлы.
        """
        session["standard_cleanup_summary"] = await self.smart_cleaner.perform_standard_cleanup()
        session["ai_cleanup_summary"] = await self.smart_cleaner.perform_deep_cleanup(
            session.get("ai_plan", {}).get("cleanup_plan", {}),
            session.get("junk_files_report", {}),
        )
        session[
            "empty_folders_summary"
        ] = await self.smart_cleaner.cleanup_all_empty_folders_async()

    async def _step_execute_action_plan(
        self, session: OptimizationSessionData, progress: ProgressCallback
    ) -> None:
        action_plan = session.get("ai_plan", {}).get("action_plan", [])
        session["debloat_summary"] = await self.windows_optimizer.execute_action_plan(
            action_plan, progress
        )

    async def _step_generate_final_report(
        self, session: OptimizationSessionData, progress: ProgressCallback
    ) -> None:
        progress(95, "Формирование отчёта...")

        cleaned_bytes = sum(
            session.get(key, {}).get("cleaned_size_bytes", 0)
            for key in ("standard_cleanup_summary", "ai_cleanup_summary")
        )
        session["final_summary"] = {
            "debloat": session.get("debloat_summary", {}),
            "cleanup": {"cleaned_size_bytes": cleaned_bytes},
            "empty_folders": session.get("empty_folders_summary", {}),
        }
        if self.ai_enabled:
            session["final_report"] = await self.ai_communicator.generate_final_report(
                session["final_summary"],
                session.get("ai_plan", {}).get("action_plan", []),
                session.get("user_profile", []),
            )
        else:
            report = AICommunicator.build_offline_report(session["final_summary"])
            session["final_report"] = report + _OFFLINE_HINT

        progress(100, "Готово!")

    # --- Фоновые задачи ---------------------------------------------------

    def _spawn_background(self, coro: Any) -> None:
        """Запускает фоновую задачу, удерживая ссылку до её завершения."""
        task = asyncio.create_task(coro)
        self.background_tasks.add(task)
        task.add_done_callback(self.background_tasks.discard)

    async def _run_ai_self_reflection(self, session: OptimizationSessionData) -> None:
        """Просит ИИ предложить улучшения продукта по итогам сессии."""
        if not self.ai_enabled:
            return
        try:
            suggestions = await self.ai_communicator.get_ai_suggestions_for_improvement(
                user_profile=session.get("user_profile"),
                plan=session.get("ai_plan"),
                summary=session.get("final_summary"),
            )
            logger.info("Предложения ИИ по улучшению:\n%s", suggestions)
        except Exception as exc:
            logger.warning("Не удалось получить предложения по улучшению: %s", exc)

    async def shutdown(self, **_kwargs: Any) -> None:
        """
        Останавливает фоновые задачи и пул процессов.

        Ожидание фоновых задач ограничено: саморефлексия ИИ — это сетевой
        запрос с собственным таймаутом в две минуты, и ради него нельзя
        задерживать закрытие приложения. Пул процессов закрывается в любом
        случае, иначе после выхода остаются висеть дочерние процессы.
        """
        if self.background_tasks:
            tasks = list(self.background_tasks)
            logger.info("Ожидание завершения %d фоновых задач...", len(tasks))

            _, pending = await asyncio.wait(tasks, timeout=self.BACKGROUND_WAIT_SECONDS)
            for task in pending:
                logger.info("Фоновая задача не успела завершиться — отменяем.")
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

        await self.worker_pool.shutdown()
        logger.info("Ядро остановлено.")


def _make_cancellation_check(
    is_cancelled: Callable[[], bool] | None,
) -> Callable[[], None]:
    """Создаёт функцию, выбрасывающую CancelledError по запросу отмены."""

    def check() -> None:
        if is_cancelled is not None and is_cancelled():
            raise asyncio.CancelledError

    return check
