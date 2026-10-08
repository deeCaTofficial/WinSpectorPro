# src/winspector/core/modules/service_control.py
"""
Управление службами Windows напрямую через API диспетчера служб (SCM).

Раньше каждое действие над службой запускало отдельный `powershell.exe` —
около секунды и ~190 МБ памяти на процесс, а план из десятка действий
поднимал десяток интерпретаторов разом. Вызовы `win32service` делают то же
за миллисекунды в текущем процессе.

Побочный выигрыш в безопасности: имя службы уходит в API аргументом, а не
подставляется в текст команды. Проверка `is_safe_identifier` в вызывающем
коде остаётся как второй барьер.

Семантика повторяет прежние команды PowerShell:

* ``stop``       — `Stop-Service -Force`: сначала останавливаются зависимые
                   службы, затем сама служба; ожидание до `STOP_TIMEOUT`.
* ``set_manual`` — `Set-Service -StartupType Manual`.
* ``disable``    — `Stop-Service -Force -ErrorAction SilentlyContinue`, затем
                   `Set-Service -StartupType Disabled`: неудачная остановка
                   не отменяет отключение — служба не поднимется после
                   перезагрузки, ради этого действие и существует.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import pywintypes
import win32service
import winerror

logger = logging.getLogger(__name__)

SUPPORTED_ACTIONS = frozenset({"stop", "set_manual", "disable"})

# Столько же ждёт `Stop-Service` по умолчанию.
STOP_TIMEOUT = 30.0
_POLL_INTERVAL = 0.25

_START_TYPES = {
    "set_manual": win32service.SERVICE_DEMAND_START,
    "disable": win32service.SERVICE_DISABLED,
}

# Права ровно под выполняемые операции: лишние права здесь ни к чему, а
# `SERVICE_ALL_ACCESS` иногда не выдаётся даже администратору.
_SERVICE_ACCESS = (
    win32service.SERVICE_STOP
    | win32service.SERVICE_QUERY_STATUS
    | win32service.SERVICE_CHANGE_CONFIG
    | win32service.SERVICE_ENUMERATE_DEPENDENTS
)
_DEPENDENT_ACCESS = win32service.SERVICE_STOP | win32service.SERVICE_QUERY_STATUS


class ServiceControlError(Exception):
    """Действие над службой не выполнено; сообщение пригодно для отчёта."""


def apply_service_action(name: str, action: str, *, stop_timeout: float = STOP_TIMEOUT) -> None:
    """
    Выполняет действие над службой.

    Raises:
        ServiceControlError: служба не найдена, нет прав, не остановилась
            за отведённое время или действие неизвестно.
    """
    if action not in SUPPORTED_ACTIONS:
        raise ServiceControlError(f"Неизвестное действие над службой: {action!r}")

    try:
        scm = win32service.OpenSCManager(None, None, win32service.SC_MANAGER_CONNECT)
    except pywintypes.error as exc:
        raise ServiceControlError(f"Диспетчер служб недоступен: {_describe(exc)}") from exc

    try:
        handle = _open_service(scm, name, _SERVICE_ACCESS)
        try:
            if action == "stop":
                _stop_with_dependents(scm, handle, name, stop_timeout)
                return

            if action == "disable":
                try:
                    _stop_with_dependents(scm, handle, name, stop_timeout)
                except ServiceControlError as exc:
                    logger.warning(
                        "Служба '%s' не остановлена (%s) — отключаем запуск без остановки.",
                        name,
                        exc,
                    )

            _set_start_type(handle, name, _START_TYPES[action])
        finally:
            win32service.CloseServiceHandle(handle)
    finally:
        win32service.CloseServiceHandle(scm)


# --- Примитивы --------------------------------------------------------------


def _open_service(scm: Any, name: str, access: int) -> Any:
    try:
        return win32service.OpenService(scm, name, access)
    except pywintypes.error as exc:
        if exc.winerror in (
            winerror.ERROR_SERVICE_DOES_NOT_EXIST,
            winerror.ERROR_INVALID_NAME,
        ):
            raise ServiceControlError(f"Служба '{name}' не найдена в системе.") from exc
        raise ServiceControlError(f"Не удалось открыть службу '{name}': {_describe(exc)}") from exc


def _set_start_type(handle: Any, name: str, start_type: int) -> None:
    no_change = win32service.SERVICE_NO_CHANGE
    try:
        win32service.ChangeServiceConfig(
            handle,
            no_change,  # тип службы
            start_type,
            no_change,  # реакция на ошибку запуска
            None,  # исполняемый файл
            None,  # группа порядка загрузки
            0,  # tag id не запрашивается
            None,  # зависимости
            None,  # учётная запись
            None,  # пароль
            None,  # отображаемое имя
        )
    except pywintypes.error as exc:
        raise ServiceControlError(
            f"Не удалось изменить тип запуска службы '{name}': {_describe(exc)}"
        ) from exc


def _stop_with_dependents(scm: Any, handle: Any, name: str, timeout: float) -> None:
    """Останавливает активные зависимые службы, затем саму службу."""
    deadline = time.monotonic() + timeout

    try:
        dependents = win32service.EnumDependentServices(handle, win32service.SERVICE_ACTIVE)
    except pywintypes.error as exc:
        raise ServiceControlError(
            f"Не удалось получить зависимости службы '{name}': {_describe(exc)}"
        ) from exc

    for dependent_name, _display_name, _status in dependents:
        dependent = _open_service(scm, dependent_name, _DEPENDENT_ACCESS)
        try:
            _stop_one(dependent, dependent_name, deadline)
        finally:
            win32service.CloseServiceHandle(dependent)

    _stop_one(handle, name, deadline)


def _stop_one(handle: Any, name: str, deadline: float) -> None:
    try:
        win32service.ControlService(handle, win32service.SERVICE_CONTROL_STOP)
    except pywintypes.error as exc:
        if exc.winerror == winerror.ERROR_SERVICE_NOT_ACTIVE:
            return  # уже остановлена — как и для Stop-Service, это не ошибка
        raise ServiceControlError(
            f"Не удалось остановить службу '{name}': {_describe(exc)}"
        ) from exc

    while _current_state(handle, name) != win32service.SERVICE_STOPPED:
        if time.monotonic() >= deadline:
            raise ServiceControlError(f"Служба '{name}' не остановилась за отведённое время.")
        time.sleep(_POLL_INTERVAL)


def _current_state(handle: Any, name: str) -> int:
    try:
        return win32service.QueryServiceStatus(handle)[1]
    except pywintypes.error as exc:
        raise ServiceControlError(
            f"Не удалось запросить состояние службы '{name}': {_describe(exc)}"
        ) from exc


def _describe(exc: pywintypes.error) -> str:
    """Человекочитаемое описание ошибки Win32 с кодом."""
    return f"{exc.strerror or exc.funcname} (код {exc.winerror})"
