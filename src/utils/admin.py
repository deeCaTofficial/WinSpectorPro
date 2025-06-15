# src/utils/admin.py
import ctypes
import sys
import logging

logger = logging.getLogger(__name__)

def check_admin_rights() -> bool:
    """Проверяет, запущено ли приложение с правами администратора."""
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except AttributeError:
        # На системах, отличных от Windows, или в тестовых окружениях
        return False

def relaunch_as_admin() -> None:
    """Пытается перезапустить приложение с правами администратора."""
    logger.info("Отправлен запрос на перезапуск с правами администратора.")
    try:
        ctypes.windll.shell32.ShellExecuteW(
            None, "runas", sys.executable, " ".join(sys.argv), None, 1
        )
    except Exception as e:
        logger.error(f"Не удалось перезапустить с правами администратора: {e}")
        # В реальном приложении здесь можно было бы показать QMessageBox,
        # но для утилиты достаточно лога.