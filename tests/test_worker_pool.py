# tests/test_worker_pool.py
"""
Тесты пула процессов.

Главное требование: обращение к воркеру не должно блокировать event loop —
именно из-за этого раньше замирал интерфейс на время WMI-запросов.
"""

from __future__ import annotations

import asyncio
import os
import time

import pytest

from winspector.core.worker_pool import WorkerPool


def _echo(value: int) -> int:
    """Функция верхнего уровня: только такие можно передать в подпроцесс."""
    return value * 2


def _slow(duration: float) -> str:
    time.sleep(duration)
    return "готово"


def _pid() -> int:
    return os.getpid()


@pytest.mark.slow
class TestWorkerPool:
    async def test_runs_function_in_separate_process(self):
        pool = WorkerPool()
        try:
            assert await pool.run(_echo, 21) == 42
            assert await pool.run(_pid) != os.getpid()
        finally:
            await pool.shutdown()

    async def test_event_loop_stays_responsive(self):
        """
        Пока воркер занят, цикл событий должен продолжать выполнять задачи.
        Прежняя реализация с `with ProcessPoolExecutor(...)` блокировала поток
        на `shutdown(wait=True)`, и интерфейс замирал.
        """
        pool = WorkerPool()
        ticks = 0

        async def heartbeat() -> None:
            nonlocal ticks
            while True:
                await asyncio.sleep(0.01)
                ticks += 1

        beat = asyncio.create_task(heartbeat())
        try:
            assert await pool.run(_slow, 0.5) == "готово"
        finally:
            beat.cancel()
            await pool.shutdown()

        assert ticks > 5, f"цикл событий был заблокирован (тиков: {ticks})"

    async def test_pool_is_reused_across_calls(self):
        pool = WorkerPool()
        try:
            first = await pool.run(_pid)
            second = await pool.run(_pid)
            assert first == second, "воркер должен переиспользоваться"
        finally:
            await pool.shutdown()

    async def test_concurrent_calls_are_serialised_safely(self):
        pool = WorkerPool()
        try:
            results = await asyncio.gather(*(pool.run(_echo, i) for i in range(5)))
            assert results == [0, 2, 4, 6, 8]
        finally:
            await pool.shutdown()

    async def test_shutdown_is_idempotent(self):
        pool = WorkerPool()
        await pool.run(_echo, 1)
        await pool.shutdown()
        await pool.shutdown()  # повторный вызов безопасен

    async def test_shutdown_without_use_is_safe(self):
        await WorkerPool().shutdown()

    async def test_pool_recreated_after_shutdown(self):
        pool = WorkerPool()
        await pool.run(_echo, 1)
        await pool.shutdown()
        try:
            assert await pool.run(_echo, 5) == 10
        finally:
            await pool.shutdown()
