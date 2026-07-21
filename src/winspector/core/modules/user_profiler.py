# src/winspector/core/modules/user_profiler.py
"""
Модуль для сбора неперсональных данных о системе с целью
построения детального профиля использования компьютера.
"""

from __future__ import annotations

import asyncio
import logging
import os
import winreg
from pathlib import Path
from typing import Any

from ..wmi_workers import get_hardware_info_worker
from ..worker_pool import WorkerPool

logger = logging.getLogger(__name__)

# Ярлыков в меню «Пуск» бывают тысячи; для определения профиля хватает выборки,
# а полный список только раздувает промпт.
MAX_SHORTCUTS_PER_LOCATION = 150
MAX_SOFTWARE_ENTRIES = 400


class UserProfiler:
    """
    Собирает обезличенные признаки использования ПК: оборудование,
    установленное ПО, ярлыки, наличие ключевых пользовательских папок.
    """

    def __init__(self, worker_pool: WorkerPool | None = None) -> None:
        logger.info("Инициализация UserProfiler...")
        self._worker_pool = worker_pool or WorkerPool()

    async def get_system_profile(self) -> dict[str, Any]:
        """
        Параллельно собирает все признаки системы.

        Сбор оборудования идёт в отдельном процессе (WMI/COM), остальное —
        в потоках. Ошибка одного источника не срывает профилирование целиком.
        """
        logger.info("Начало сбора данных для профилирования системы.")

        collectors: dict[str, Any] = {
            "hardware": self._worker_pool.run(get_hardware_info_worker),
            "installed_software": asyncio.to_thread(self._get_installed_software_from_registry),
            "environment_variables": asyncio.to_thread(self._get_environment_variables),
            "shortcuts": asyncio.to_thread(self._get_desktop_and_start_menu_shortcuts),
            "user_folder_stats": asyncio.to_thread(self._get_user_folder_stats),
            "default_browser": asyncio.to_thread(self._get_default_browser),
        }

        results = await asyncio.gather(*collectors.values(), return_exceptions=True)

        profile: dict[str, Any] = {}
        for key, result in zip(collectors.keys(), results, strict=True):
            if isinstance(result, BaseException):
                logger.error("Ошибка при сборе '%s': %s", key, result)
                profile[key] = {"error": str(result)}
            else:
                profile[key] = result

        logger.info("Профилирование системы завершено.")
        return profile

    def _get_installed_software_from_registry(self) -> dict[str, list[str]]:
        """
        Собирает список установленного ПО из реестра Windows,
        фильтруя системные компоненты и обновления.
        """
        logger.debug("Сбор списка установленного ПО из реестра...")
        installed_software: set[str] = set()

        uninstall_paths = [
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
            (
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Wow6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
            ),
            (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        ]

        for hkey, path in uninstall_paths:
            try:
                with winreg.OpenKey(hkey, path, 0, winreg.KEY_READ) as key:
                    for i in range(winreg.QueryInfoKey(key)[0]):
                        try:
                            subkey_name = winreg.EnumKey(key, i)
                            with winreg.OpenKey(key, subkey_name) as subkey:
                                try:
                                    if winreg.QueryValueEx(subkey, "SystemComponent")[0] == 1:
                                        continue
                                except (OSError, FileNotFoundError):
                                    pass

                                try:
                                    release_type = (
                                        winreg.QueryValueEx(subkey, "ReleaseType")[0] or ""
                                    )
                                    if "Update" in release_type or "Hotfix" in release_type:
                                        continue
                                except (OSError, FileNotFoundError):
                                    pass

                                display_name = winreg.QueryValueEx(subkey, "DisplayName")[0]
                                if (
                                    display_name
                                    and not display_name.startswith("KB")
                                    and "Update" not in display_name
                                ):
                                    installed_software.add(display_name.strip())
                        except (OSError, FileNotFoundError):
                            continue
            except FileNotFoundError:
                continue

        logger.debug("Найдено %d записей о ПО в реестре.", len(installed_software))
        return {"software_list": sorted(installed_software)[:MAX_SOFTWARE_ENTRIES]}

    def _get_environment_variables(self) -> dict[str, str]:
        """
        Собирает системные и пользовательские переменные окружения,
        которые могут указывать на среду разработки.
        """
        logger.debug("Сбор переменных окружения...")
        interesting_vars = [
            "PATH",
            "JAVA_HOME",
            "PYTHONPATH",
            "GOPATH",
            "NODE_PATH",
            "ANDROID_HOME",
            "VCPKG_ROOT",
            "QT_DIR",
        ]

        env_vars: dict[str, str] = {}
        for var in interesting_vars:
            value = os.getenv(var)
            if value:
                env_vars[var] = value

        return env_vars

    def _get_desktop_and_start_menu_shortcuts(self) -> dict[str, list[str]]:
        """Сканирует рабочий стол и меню 'Пуск' на наличие ярлыков."""
        logger.debug("Сбор ярлыков с рабочего стола и из меню 'Пуск'...")
        shortcut_locations = {
            "user_desktop": Path(os.path.expandvars("%USERPROFILE%\\Desktop")),
            "public_desktop": Path(os.path.expandvars("%PUBLIC%\\Desktop")),
            "user_start_menu": Path(
                os.path.expandvars("%APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs")
            ),
            "common_start_menu": Path(
                os.path.expandvars("%PROGRAMDATA%\\Microsoft\\Windows\\Start Menu\\Programs")
            ),
        }

        shortcuts: dict[str, list[str]] = {key: [] for key in shortcut_locations}
        for name, path in shortcut_locations.items():
            if not path.is_dir():
                continue
            try:
                for item in path.rglob("*.lnk"):
                    shortcuts[name].append(item.stem)
                    if len(shortcuts[name]) >= MAX_SHORTCUTS_PER_LOCATION:
                        break
            except OSError as exc:
                logger.warning("Не удалось просканировать '%s': %s", path, exc)

        return shortcuts

    def _get_user_folder_stats(self) -> dict[str, Any]:
        """Собирает статистику по ключевым пользовательским папкам."""
        logger.debug("Сбор статистики по пользовательским папкам...")
        stats = {}

        folders_to_check = {
            "documents": Path(os.path.expandvars("%USERPROFILE%\\Documents")),
            "pictures": Path(os.path.expandvars("%USERPROFILE%\\Pictures")),
            "videos": Path(os.path.expandvars("%USERPROFILE%\\Videos")),
            "saved_games": Path(os.path.expandvars("%USERPROFILE%\\Saved Games")),
            "source_repos": Path(os.path.expandvars("%USERPROFILE%\\source\\repos")),
        }

        for name, path in folders_to_check.items():
            if path.is_dir():
                try:
                    # Просто проверяем, есть ли в папке хоть что-то
                    has_content = any(path.iterdir())
                    stats[name] = {"exists": True, "has_content": has_content}
                except OSError:
                    stats[name] = {"exists": True, "has_content": False, "error": "access_denied"}
            else:
                stats[name] = {"exists": False, "has_content": False}

        # Проверяем наличие конфигурационных файлов разработчика
        git_config = Path(os.path.expandvars("%USERPROFILE%\\.gitconfig"))
        if git_config.exists():
            stats["git_config_exists"] = True

        return stats

    def _get_default_browser(self) -> str | None:
        """Определяет браузер по умолчанию из реестра Windows."""
        logger.debug("Определение браузера по умолчанию...")
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice",
            ) as key:
                prog_id = winreg.QueryValueEx(key, "ProgId")[0]

            with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, rf"{prog_id}\shell\open\command") as key:
                command_path = winreg.QueryValue(key, None)
                # Извлекаем имя исполняемого файла, учитывая возможные кавычки
                parts = command_path.split('"')
                browser_path = parts[1] if len(parts) > 1 else parts[0]
                return Path(browser_path).name
        except FileNotFoundError:
            logger.warning("Не удалось определить браузер по умолчанию: ключ в реестре не найден.")
            return None
        except Exception as e:
            logger.error("Ошибка при определении браузера по умолчанию: %s", e)
            return None
