# src/winspector/core/modules/wmi_base.py
"""
Содержит базовый класс для модулей, работающих с Windows Management Instrumentation (WMI).

Этот класс решает две ключевые задачи:
1.  Инкапсулирует логику "ленивой" инициализации объекта WMI, чтобы избежать
    дублирования кода.
2.  Обеспечивает потокобезопасность, гарантируя, что COM-объект WMI
    создается в том же потоке, где он будет использоваться.
"""
import os
import wmi
import logging
import threading
from typing import Optional, Any

# Импорт pythoncom важен для приложений, использующих COM в многопоточной среде.
# Хотя CoInitialize/CoUninitialize вызываются в AsyncWorker (GUI-слой),
# наличие этого импорта здесь служит полезным напоминанием о зависимости.
try:
    import pythoncom
except ImportError:
    # Позволяет коду работать в окружениях без pywin32 (например, при
    # запуске тестов на Linux в CI/CD), где этот импорт не нужен.
    pythoncom = None

logger = logging.getLogger(__name__)


class WMIBase:
    """
    Базовый класс, предоставляющий потокобезопасный доступ к WMI.
    
    Дочерние классы (например, UserProfiler, WindowsOptimizer) должны
    наследоваться от этого класса и вызывать `super().__init__()`.
    Для всех обращений к WMI следует использовать свойство `self.wmi_instance`.
    """
    
    def __init__(self):
        """Инициализирует базовый класс, но не сам объект WMI."""
        # Используем 'Any' для типа _wmi, так как статическая типизация
        # библиотеки 'wmi' ненадежна и вызывает проблемы с анализаторами.
        self._wmi: Optional[Any] = None

    @property
    def wmi_instance(self) -> Any:
        """
        Лениво инициализирует и возвращает экземпляр WMI.

        Этот property-метод гарантирует, что `wmi.WMI()` вызывается только
        при первом обращении и в контексте того потока, который его запросил.
        Это решает классическую проблему с COM-апартаментами при работе
        с WMI в многопоточных приложениях.

        Returns:
            Активный экземпляр WMI.

        Raises:
            wmi.x_wmi: Если не удалось подключиться к WMI (например,
                       служба WMI остановлена или повреждена).
        """
        if self._wmi is None:
            thread_id = threading.get_ident()
            logger.debug(f"Инициализация нового экземпляра WMI для потока {thread_id}...")
            
            try:
                # Этот вызов может занять некоторое время и является блокирующим.
                # Он должен выполняться внутри `asyncio.to_thread`.
                self._wmi = wmi.WMI()
                logger.info(f"Экземпляр WMI успешно создан для потока {thread_id}.")
            except wmi.x_wmi as e:
                logger.error(f"Критическая ошибка при инициализации WMI в потоке {thread_id}: {e}", exc_info=True)
                # Перевыбрасываем исключение, чтобы вышестоящий код (например,
                # в WinSpectorCore) мог его поймать и обработать.
                raise
        
        return self._wmi