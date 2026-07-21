# src/winspector/core/worker_pool.py
"""
Пул процессов для изолированного выполнения WMI-запросов.

Зачем отдельный процесс: библиотека `wmi` работает через COM, а COM плохо
уживается с многопоточным asyncio-приложением на PyQt. Отдельный процесс даёт
чистое COM-окружение и защищает GUI от падений внутри WMI.

Почему пул живёт долго: создание `ProcessPoolExecutor` в блоке `with` внутри
корутины приводит к вызову `shutdown(wait=True)` при выходе из блока, а он
блокирует поток event loop — интерфейс замирает на всё время запроса.
Поэтому пул создаётся один раз и закрывается при завершении приложения.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class WorkerPool:
    """Ленивый пул процессов с корректным завершением."""

    def __init__(self, max_workers: int = 1) -> None:
        self._max_workers = max_workers
        self._pool: ProcessPoolExecutor | None = None
        self._lock = asyncio.Lock()

    async def run(self, func: Callable[..., T], *args: Any) -> T:
        """Выполняет функцию в отдельном процессе, не блокируя event loop."""
        pool = await self._ensure_pool()
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(pool, func, *args)

    async def _ensure_pool(self) -> ProcessPoolExecutor:
        async with self._lock:
            if self._pool is None:
                logger.debug("Создание пула процессов (%d воркеров).", self._max_workers)
                self._pool = ProcessPoolExecutor(max_workers=self._max_workers)
            return self._pool

    async def shutdown(self) -> None:
        """Закрывает пул. Ожидание вынесено в поток, чтобы не блокировать loop."""
        pool, self._pool = self._pool, None
        if pool is None:
            return
        logger.debug("Завершение пула процессов.")
        await asyncio.to_thread(pool.shutdown, True)
