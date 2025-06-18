# src/winspector/core/modules/smart_cleaner.py
"""
Модуль для интеллектуальной очистки системы...
Финальная версия с максимально надежным удалением пустых директорий.
"""
import os
import shutil
import asyncio
import logging
import fnmatch
import subprocess
from typing import List, Dict, Any, Tuple
from pathlib import Path
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class SmartCleaner:
    PROTECTED_EXTENSIONS = {'.exe', '.dll', '.sys', '.py', '.ps1', '.bat', '.cmd', '.jar', '.msi'}
    # ### УЛУЧШЕНИЕ: Список файлов, которые не мешают папке считаться пустой ###
    IGNORED_FILES_ON_EMPTY_CHECK = {'thumbs.db', 'desktop.ini'}
    # ### УЛУЧШЕНИЕ: Добавляем список защищенных системных папок ###
    PROTECTED_SYSTEM_FOLDERS = {
        'accountpictures', 'administrative tools', 'application shortcuts',
        'burn', 'cd burning', 'cookies', 'credentials', 'cryptneturlcache',
        'devicelds', 'dpapimasterkeys', 'en-us', 'ru-ru', 'sendto',
        'start menu', 'templates', 'windows'
    }

    def __init__(self, cleanup_rules: List[Dict]):
        """
        Инициализирует модуль очистки.

        Args:
            cleanup_rules: Список правил для очистки из cleanup_rules.yaml.
        """
        logger.info("Инициализация SmartCleaner (Advanced)...")
        self.rules = {rule['category_id']: rule for rule in cleanup_rules if 'category_id' in rule}

    async def find_junk_files_deep(self) -> Dict[str, Any]:
        """
        Проводит глубокий поиск ненужных файлов, автоматически определяя,
        является ли путь папкой для полной очистки или маской для поиска файлов.
        """
        logger.info("Начало глубокого поиска ненужных файлов.")
        
        tasks = [self._scan_rule(category_id, rule) for category_id, rule in self.rules.items()]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        junk_summary: Dict[str, Any] = {}
        for res in results:
            if isinstance(res, Exception):
                logger.error(f"Ошибка при поиске мусора: {res}", exc_info=res)
                continue
            if res and res.get("total_size", 0) > 0:
                category = res.pop("category_id")
                junk_summary[category] = res

        logger.info(f"Глубокий поиск завершен. Найдено {len(junk_summary)} категорий мусора.")
        logger.debug(f"Сводка по найденному мусору: {junk_summary}")
        return junk_summary

    async def perform_standard_cleanup(self) -> Dict[str, Any]:
        """
        Выполняет стандартную, детерминированную очистку наиболее
        распространенных временных файлов и кэшей Windows.
        """
        logger.info("Начало стандартной системной очистки...")
        
        standard_plan = {
            "temp_files": {
                "paths": ["%WINDIR%\\Temp", "%TEMP%"],
                "type": "folder_content"
            },
            "windows_update_cache": {
                "paths": ["%WINDIR%\\SoftwareDistribution\\Download"],
                "type": "folder_content"
            },
            "windows_error_reports": {
                "paths": [
                    "%PROGRAMDATA%\\Microsoft\\Windows\\WER\\ReportArchive",
                    "%PROGRAMDATA%\\Microsoft\\Windows\\WER\\ReportQueue"
                ],
                "type": "folder_content"
            },
            "memory_dumps": {
                "paths": ["%WINDIR%\\MEMORY.DMP", "%WINDIR%\\Minidump\\*.*"],
                "type": "files_by_mask"
            },
            "thumbnail_cache": {
                "paths": ["%LOCALAPPDATA%\\Microsoft\\Windows\\Explorer\\thumbcache_*.db"],
                "type": "files_by_mask"
            },
            "dns_cache": {
                "type": "command",
                "command": "ipconfig /flushdns"
            }
        }

        summary = {"cleaned_size_bytes": 0, "deleted_files_count": 0, "errors": 0}

        for category, details in standard_plan.items():
            logger.info(f"Стандартная очистка: {category}")
            cleanup_type = details["type"]
            
            if cleanup_type == "folder_content":
                for path_str in details["paths"]:
                    size, count, errors = await asyncio.to_thread(self._clean_directory_content, Path(os.path.expandvars(path_str)))
                    summary["cleaned_size_bytes"] += size
                    summary["deleted_files_count"] += count
                    summary["errors"] += errors
            
            elif cleanup_type == "files_by_mask":
                for path_str in details["paths"]:
                    # ### ИСПРАВЛЕНИЕ: Используем правильное имя метода ###
                    found_files = await asyncio.to_thread(self._find_files_by_mask, os.path.expandvars(path_str), {})
                    for file_path, _ in found_files:
                        delete_res = await self._delete_single_file(file_path)
                        summary["cleaned_size_bytes"] += delete_res[0]
                        summary["deleted_files_count"] += delete_res[1]
                        summary["errors"] += delete_res[2]

            elif cleanup_type == "command":
                try:
                    await asyncio.to_thread(
                        subprocess.run, details["command"], shell=True, check=True, capture_output=True
                    )
                    logger.info(f"Команда '{details['command']}' успешно выполнена.")
                except Exception as e:
                    logger.warning(f"Не удалось выполнить команду '{details['command']}': {e}")
                    summary["errors"] += 1
        
        logger.info(f"Стандартная очистка завершена. Освобождено: {summary['cleaned_size_bytes'] / (1024*1024):.2f} МБ.")
        return summary

    async def _scan_rule(self, category_id: str, rule: Dict) -> Dict:
        """
        Асинхронно сканирует пути из правила. Если путь содержит маску (*), ищет файлы.
        Если это директория, измеряет ее размер.
        """
        paths_to_process = [os.path.expandvars(p) for p in rule.get("paths", [])]
        
        total_size = 0
        files_to_delete: List[str] = []
        folders_to_clean: List[str] = []

        scan_tasks = []
        for path_str in paths_to_process:
            if "*" in path_str:
                scan_tasks.append(asyncio.to_thread(self._find_files_by_mask, path_str, rule))
            else:
                scan_tasks.append(self._calculate_dir_size_safe(Path(path_str)))

        scan_results = await asyncio.gather(*scan_tasks, return_exceptions=True)

        for i, result in enumerate(scan_results):
            if isinstance(result, Exception):
                logger.warning(f"Ошибка сканирования пути {paths_to_process[i]}: {result}")
                continue
            
            if isinstance(result, list): # Результат от _find_files_by_mask
                for file_path, file_size in result:
                    total_size += file_size
                    files_to_delete.append(str(file_path))
            elif isinstance(result, int):
                if result > 0:
                    total_size += result
                    folders_to_clean.append(paths_to_process[i])
        
        report = rule.copy()
        report.update({
            "category_id": category_id,
            "total_size": total_size,
            "found_items_count": len(files_to_delete) + len(folders_to_clean),
            "files_to_delete": files_to_delete,
            "folders_to_clean": folders_to_clean,
        })
        return report

    def _find_files_by_mask(self, path_with_mask: str, rule: Dict) -> List[Tuple[Path, int]]:
        """
        Оптимизированный поиск файлов по маске с использованием os.walk
        и с применением эвристик (возраст, защищенные расширения).
        """
        parent_dir = Path(path_with_mask).parent
        mask = Path(path_with_mask).name
        age_days = rule.get("age_days")
        
        found = []
        if not parent_dir.is_dir():
            return found
            
        for root, _, filenames in os.walk(parent_dir):
            for filename in fnmatch.filter(filenames, mask):
                if Path(filename).suffix.lower() in self.PROTECTED_EXTENSIONS:
                    continue
                
                file_path = Path(root) / filename
                try:
                    stat = file_path.stat()
                    if age_days:
                        file_age = datetime.now() - datetime.fromtimestamp(stat.st_mtime)
                        if file_age < timedelta(days=age_days):
                            continue
                    
                    found.append((file_path, stat.st_size))
                except (OSError, FileNotFoundError):
                    continue
            # Предотвращаем слишком глубокий спуск, если маска не содержит рекурсивных символов
            if "*" not in Path(root).relative_to(parent_dir).as_posix():
                break # Оптимизация: если мы ищем в корне, нет смысла идти глубже

        return found

    async def perform_deep_cleanup(self, plan: Dict[str, Any]) -> Dict[str, Any]:
        """Выполняет глубокую очистку на основе плана от ИИ."""
        logger.info("Начало выполнения плана глубокой очистки.")
        summary = {"cleaned_size_bytes": 0, "deleted_files_count": 0, "errors": 0}
        
        potentially_empty_dirs = set()

        for category, details in plan.items():
            if not details.get("clean", False):
                continue
            
            logger.info(f"Очистка категории: {category}")
            
            tasks = []
            for file_path_str in details.get("files_to_delete", []):
                path = Path(file_path_str)
                potentially_empty_dirs.add(path.parent)
                tasks.append(self._delete_single_file(path))
            
            for path_str in details.get("folders_to_clean", []):
                tasks.append(asyncio.to_thread(self._clean_directory_content, Path(path_str)))
            
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            for res in results:
                if isinstance(res, Exception):
                    summary["errors"] += 1
                    logger.error(f"Ошибка во время очистки категории {category}: {res}", exc_info=res)
                elif isinstance(res, tuple):
                    size, count, errors = res
                    summary["cleaned_size_bytes"] += size
                    summary["deleted_files_count"] += count
                    summary["errors"] += errors
        
        if potentially_empty_dirs:
            logger.info(f"Проверка {len(potentially_empty_dirs)} директорий на пустоту...")
            cleanup_tasks = [asyncio.to_thread(self._cleanup_empty_dirs, d) for d in potentially_empty_dirs]
            deleted_counts, error_counts = zip(*await asyncio.gather(*cleanup_tasks, return_exceptions=True))
            summary["deleted_files_count"] += sum(c for c in deleted_counts if isinstance(c, int))
            summary["errors"] += sum(c for c in error_counts if isinstance(c, int))

        logger.info(f"Очистка завершена. Освобождено: {summary['cleaned_size_bytes'] / (1024*1024):.2f} МБ, "
                    f"ошибок: {summary['errors']}.")
        return summary

    async def cleanup_all_empty_folders_async(self, extra_paths: List[str] = None) -> Dict[str, Any]:
        """
        Асинхронно ищет и удаляет все пустые директории, используя надежный метод.
        """
        logger.info("Начало поиска и удаления пустых директорий...")
        
        base_paths_to_scan = {
            os.path.expandvars(p) for p in [
                '%APPDATA%', '%LOCALAPPDATA%', '%TEMP%',
                '%USERPROFILE%\\Downloads', '%USERPROFILE%\\Documents'
            ] if os.path.expandvars(p)
        }
        
        if extra_paths:
            base_paths_to_scan.update(os.path.expandvars(p) for p in extra_paths)

        tasks = [
            asyncio.to_thread(self._process_empty_folder_cleanup, Path(p))
            for p in base_paths_to_scan if Path(p).is_dir()
        ]
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        total_deleted_count, total_errors = 0, 0
        for res in results:
            if isinstance(res, tuple):
                total_deleted_count += res[0]
                total_errors += res[1]
        
        summary = {"deleted_folders_count": total_deleted_count, "errors": total_errors}
        logger.info(f"Очистка пустых директорий завершена. Удалено: {total_deleted_count} папок, ошибок: {total_errors}.")
        return summary

    def _is_dir_effectively_empty(self, path: Path) -> bool:
        """
        Проверяет, является ли директория пустой или содержит только
        игнорируемые системные файлы (например, Thumbs.db).
        """
        try:
            for entry in path.iterdir():
                if entry.name.lower() not in self.IGNORED_FILES_ON_EMPTY_CHECK:
                    return False # Найден значимый файл или папка
            return True # Директория пуста или содержит только игнорируемые файлы
        except (OSError, PermissionError):
            return False

    def _process_empty_folder_cleanup(self, root_path: Path) -> Tuple[int, int]:
        """
        Синхронный воркер, который обходит все подпапки и удаляет пустые,
        пропуская защищенные системные директории.
        """
        deleted_count, error_count = 0, 0
        try:
            for dirpath, _, _ in os.walk(root_path, topdown=False):
                current_dir = Path(dirpath)
                
                if current_dir.resolve() == root_path.resolve():
                    continue
                
                # ### УЛУЧШЕНИЕ: Проверка на защищенную папку ###
                if current_dir.name.lower() in self.PROTECTED_SYSTEM_FOLDERS:
                    continue

                if self._is_dir_effectively_empty(current_dir):
                    try:
                        shutil.rmtree(current_dir, ignore_errors=False)
                        logger.debug(f"Удалена пустая директория: {current_dir}")
                        deleted_count += 1
                    except (OSError, PermissionError) as e:
                        logger.warning(f"Не удалось удалить директорию '{current_dir}': {e}")
                        error_count += 1
        except Exception as e:
            logger.error(f"Ошибка при обходе директории '{root_path}': {e}")
            error_count += 1
            
        return deleted_count, error_count

    async def _delete_single_file(self, file_path: Path) -> Tuple[int, int, int]:
        """Асинхронно удаляет один файл."""
        try:
            size = file_path.stat().st_size
            file_path.unlink()
            return size, 1, 0
        except FileNotFoundError:
             # Если файл уже удален другим процессом, это не ошибка
            return 0, 0, 0
        except (OSError, PermissionError) as e:
            # ### УЛУЧШЕНИЕ: Логируем ошибку WinError 32 на уровне DEBUG ###
            if isinstance(e, OSError) and e.winerror == 32:
                logger.debug(f"Не удалось удалить занятый файл '{file_path}': {e}")
            else:
                logger.warning(f"Не удалось удалить файл '{file_path}': {e}")
            return 0, 0, 1

    def _clean_directory_content(self, path: Path) -> Tuple[int, int, int]:
        """Безопасно очищает СОДЕРЖИМОЕ директории."""
        if not path.is_dir(): return 0, 0, 0
        total_deleted_size, deleted_count, error_count = 0, 0, 0
        try:
            for item in path.iterdir():
                try:
                    if item.is_dir():
                        size = self._get_dir_size_safe(item)
                        shutil.rmtree(item, ignore_errors=True)
                        deleted_count += 1
                        total_deleted_size += size
                    elif item.is_file() or item.is_link():
                        size = item.stat().st_size
                        item.unlink()
                        deleted_count += 1
                        total_deleted_size += size
                except (OSError, PermissionError) as e:
                    logger.warning(f"Не удалось удалить '{item}': {e}")
                    error_count += 1
        except (OSError, PermissionError) as e:
            logger.warning(f"Не удалось получить доступ к директории '{path}': {e}")
            error_count += 1
        return total_deleted_size, deleted_count, error_count
    
    def _cleanup_empty_dirs(self, path: Path) -> Tuple[int, int]:
        """Рекурсивно удаляет пустые директории, поднимаясь вверх по дереву."""
        if not path.is_dir() or not os.path.exists(path):
            return 0, 0
        
        deleted_count, error_count = 0, 0
        try:
            if not any(path.iterdir()):
                logger.debug(f"Удаление пустой директории: {path}")
                path.rmdir()
                deleted_count += 1
                # Рекурсивный вызов для родительской папки
                parent_deleted, parent_errors = self._cleanup_empty_dirs(path.parent)
                deleted_count += parent_deleted
                error_count += parent_errors
        except (OSError, PermissionError) as e:
            logger.debug(f"Не удалось удалить пустую директорию '{path}': {e}")
            error_count += 1
        return deleted_count, error_count

    @staticmethod
    def _get_dir_size_safe(path: Path) -> int:
        """Рекурсивно и безопасно подсчитывает размер директории с помощью os.walk."""
        total = 0
        try:
            for dirpath, _, filenames in os.walk(path):
                for f in filenames:
                    fp = os.path.join(dirpath, f)
                    if not os.path.islink(fp):
                        try:
                            total += os.path.getsize(fp)
                        except FileNotFoundError:
                            continue
        except (OSError, FileNotFoundError):
            return 0
        return total

    async def _calculate_dir_size_safe(self, path: Path) -> int:
        """Асинхронная обертка для _get_dir_size_safe."""
        if not path.is_dir():
            return 0
        return await asyncio.to_thread(self._get_dir_size_safe, path)