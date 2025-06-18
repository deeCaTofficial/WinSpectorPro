# src/winspector/core/modules/user_profiler.py
"""
Модуль для сбора неперсональных данных о системе с целью
построения детального профиля использования компьютера.
"""
import os
import asyncio
import logging
import winreg
from typing import Dict, Any, List, Set, Optional
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor

from ..wmi_workers import get_hardware_info_worker

logger = logging.getLogger(__name__)


class UserProfiler:
    """
    Анализирует систему для определения профиля пользователя (геймер, разработчик и т.д.),
    собирая данные об оборудовании, ПО, ярлыках, пользовательских папках и настройках.
    """

    def __init__(self):
        """Инициализирует профилировщик."""
        logger.info("Инициализация UserProfiler (Advanced)...")

    async def get_system_profile(self) -> Dict[str, Any]:
        """
        Собирает многогранный, детальный профиль использования ПК,
        запуская все задачи сбора данных параллельно.
        """
        logger.info("Начало сбора данных для профилирования системы.")
        
        # Задачи, требующие отдельных процессов (WMI)
        loop = asyncio.get_running_loop()
        with ProcessPoolExecutor(max_workers=1) as pool:
            hardware_task = loop.run_in_executor(pool, get_hardware_info_worker)
        
        # Задачи, которые можно выполнить в потоках
        tasks = [
            hardware_task,
            asyncio.to_thread(self._get_installed_software_from_registry),
            asyncio.to_thread(self._get_environment_variables),
            asyncio.to_thread(self._get_desktop_and_start_menu_shortcuts),
            asyncio.to_thread(self._get_user_folder_stats),
            asyncio.to_thread(self._get_default_browser),
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Сопоставляем результаты с именами для ясности
        profile_data_keys = ["hardware", "installed_software", "environment_variables", 
                             "shortcuts", "user_folder_stats", "default_browser"]
        profile = {}

        for key, result in zip(profile_data_keys, results):
            if isinstance(result, Exception):
                logger.error(f"Ошибка при сборе данных для '{key}': {result}")
                profile[key] = {"error": str(result)}
            else:
                profile[key] = result
        
        logger.info("Профилирование системы завершено.")
        logger.debug(f"Собранный профиль: {profile}")
        return profile

    def _get_installed_software_from_registry(self) -> Dict[str, List[str]]:
        """
        Собирает список установленного ПО из реестра Windows,
        фильтруя системные компоненты и обновления.
        """
        logger.debug("Сбор списка установленного ПО из реестра...")
        installed_software: Set[str] = set()
        
        uninstall_paths = [
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Wow6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
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
                                    release_type = winreg.QueryValueEx(subkey, "ReleaseType")[0] or ""
                                    if "Update" in release_type or "Hotfix" in release_type:
                                        continue
                                except (OSError, FileNotFoundError):
                                    pass
                                
                                display_name = winreg.QueryValueEx(subkey, "DisplayName")[0]
                                if display_name and not display_name.startswith("KB") and "Update" not in display_name:
                                    installed_software.add(display_name.strip())
                        except (OSError, FileNotFoundError):
                            continue
            except FileNotFoundError:
                continue
        
        logger.debug(f"Найдено {len(installed_software)} записей о ПО в реестре.")
        return {"software_list": sorted(list(installed_software))}

    def _get_environment_variables(self) -> Dict[str, str]:
        """
        Собирает системные и пользовательские переменные окружения,
        которые могут указывать на среду разработки.
        """
        logger.debug("Сбор переменных окружения...")
        interesting_vars = [
            'PATH', 'JAVA_HOME', 'PYTHONPATH', 'GOPATH', 'NODE_PATH',
            'ANDROID_HOME', 'VCPKG_ROOT', 'QT_DIR'
        ]
        
        env_vars: Dict[str, str] = {}
        for var in interesting_vars:
            value = os.getenv(var)
            if value:
                env_vars[var] = value
        
        return env_vars

    def _get_desktop_and_start_menu_shortcuts(self) -> Dict[str, List[str]]:
        """Сканирует рабочий стол и меню 'Пуск' на наличие ярлыков."""
        logger.debug("Сбор ярлыков с рабочего стола и из меню 'Пуск'...")
        shortcut_locations = {
            "user_desktop": Path(os.path.expandvars("%USERPROFILE%\\Desktop")),
            "public_desktop": Path(os.path.expandvars("%PUBLIC%\\Desktop")),
            "user_start_menu": Path(os.path.expandvars("%APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs")),
            "common_start_menu": Path(os.path.expandvars("%PROGRAMDATA%\\Microsoft\\Windows\\Start Menu\\Programs")),
        }
        
        shortcuts = {key: [] for key in shortcut_locations}
        for name, path in shortcut_locations.items():
            if path.is_dir():
                try:
                    for item in path.rglob("*.lnk"):
                        shortcuts[name].append(item.stem)
                except (OSError, PermissionError) as e:
                    logger.warning(f"Не удалось просканировать директорию ярлыков '{path}': {e}")
        
        return shortcuts

    def _get_user_folder_stats(self) -> Dict[str, Any]:
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
                except (OSError, PermissionError):
                    stats[name] = {"exists": True, "has_content": False, "error": "access_denied"}
            else:
                stats[name] = {"exists": False, "has_content": False}
        
        # Проверяем наличие конфигурационных файлов разработчика
        git_config = Path(os.path.expandvars("%USERPROFILE%\\.gitconfig"))
        if git_config.exists():
            stats["git_config_exists"] = True
            
        return stats

    def _get_default_browser(self) -> Optional[str]:
        """Определяет браузер по умолчанию из реестра Windows."""
        logger.debug("Определение браузера по умолчанию...")
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice") as key:
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
            logger.error(f"Ошибка при определении браузера по умолчанию: {e}")
            return None