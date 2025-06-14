# src/winspector/core/modules/smart_cleaner.py
"""
Модуль для интеллектуальной очистки системы от временных файлов,
кэшей, логов и других "мусорных" данных.
"""
import os
import shutil
import asyncio
import logging
from typing import List, Dict, Any, Tuple
from pathlib import Path
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class SmartCleaner:
    """
    Выполняет поиск и удаление ненужных файлов на основе правил
    из базы знаний.
    """
    
    def __init__(self, heuristic_rules: Dict[str, Any]):
        """
        Инициализирует модуль очистки.

        Args:
            heuristic_rules: Словарь с правилами для эвристического и прямого поиска.
        """
        logger.info("Инициализация SmartCleaner (Advanced)...")
        self.rules = heuristic_rules

    async def find_junk_files_deep(self) -> Dict[str, Any]:
        """
        Проводит глубокий поиск ненужных файлов на основе всех правил.
        
        Returns:
            Словарь с отчетом о найденных категориях "мусора".
        """
        logger.info("Начало глубокого поиска ненужных файлов.")
        
        tasks = []
        for category, rule in self.rules.items():
            if rule.get("heuristic", False):
                # Эвристический поиск по параметрам
                task = asyncio.to_thread(self._heuristic_search, category, rule)
            else:
                # Поиск по прямым путям
                task = self._direct_path_search(category, rule)
            tasks.append(task)
            
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        junk_summary: Dict[str, Any] = {}
        for res in results:
            if isinstance(res, Exception):
                logger.error(f"Ошибка при поиске мусора: {res}", exc_info=res)
                continue
            if res and res.get("size", 0) > 0:
                category = res.pop("category")
                junk_summary[category] = res

        logger.info(f"Глубокий поиск завершен. Найдено {len(junk_summary)} категорий мусора.")
        logger.debug(f"Сводка по найденному мусору: {junk_summary}")
        return junk_summary

    def _heuristic_search(self, category: str, rule: Dict) -> Dict:
        """Синхронная функция для эвристического поиска в заданном пути."""
        search_path_str = os.path.expandvars(rule.get("search_path", ""))
        if not search_path_str:
            return {}
            
        search_path = Path(search_path_str)
        logger.debug(f"Эвристический поиск для '{category}' в: {search_path}")
        
        result = {"category": category, "size": 0, "count": 0, "files_to_delete": []}
        extensions = tuple(rule.get("extensions", []))
        age_threshold = timedelta(days=rule.get("age_days", 9999))
        
        try:
            for entry in os.scandir(search_path):
                # Рекурсивный поиск пока не поддерживается для простоты, но можно добавить
                if entry.is_file() and entry.name.lower().endswith(extensions):
                    try:
                        stat = entry.stat()
                        file_age = datetime.now() - datetime.fromtimestamp(stat.st_mtime)
                        if file_age > age_threshold:
                            result["size"] += stat.st_size
                            result["count"] += 1
                            result["files_to_delete"].append(entry.path)
                    except (OSError, FileNotFoundError):
                        continue
        except FileNotFoundError:
            logger.warning(f"Путь для эвристического поиска '{search_path}' не найден.")
        
        return result

    async def _direct_path_search(self, category: str, rule: Dict) -> Dict:
        """Асинхронно измеряет размер директорий, указанных напрямую."""
        paths_to_check = [os.path.expandvars(p) for p in rule.get("paths", [])]
        total_size = 0
        
        size_tasks = [self._calculate_dir_size_safe(Path(p)) for p in paths_to_check if p]
        sizes = await asyncio.gather(*size_tasks)
        total_size = sum(sizes)

        return {
            "category": category,
            "size": total_size,
            "paths_to_clean": paths_to_check,
            "description": rule.get("description", "")
        }

    async def perform_deep_cleanup(self, plan: Dict[str, Any]) -> Dict[str, Any]:
        """Выполняет глубокую очистку на основе плана от ИИ."""
        logger.info("Начало выполнения плана глубокой очистки.")
        summary = {"cleaned_size_bytes": 0, "deleted_files_count": 0, "errors": 0}

        for category, details in plan.items():
            if not details.get("clean", False):
                continue
            
            logger.info(f"Очистка категории: {category}")
            # Если это эвристический поиск, удаляем файлы по списку
            if "files_to_delete" in details:
                for file_path in details["files_to_delete"]:
                    try:
                        file = Path(file_path)
                        size = file.stat().st_size
                        file.unlink() # Используем unlink для файлов
                        summary["cleaned_size_bytes"] += size
                        summary["deleted_files_count"] += 1
                    except (OSError, PermissionError) as e:
                        logger.warning(f"Не удалось удалить файл '{file_path}': {e}")
                        summary["errors"] += 1
            
            # Если это очистка директорий по прямому пути
            elif "paths_to_clean" in details:
                for path_str in details["paths_to_clean"]:
                    size, errors = await asyncio.to_thread(self._clean_directory_content, Path(path_str))
                    summary["cleaned_size_bytes"] += size
                    summary["errors"] += errors
                    
        logger.info(f"Очистка завершена. Освобождено: {summary['cleaned_size_bytes'] / (1024*1024):.2f} МБ, "
                    f"ошибок: {summary['errors']}.")
        return summary

    def _clean_directory_content(self, path: Path) -> Tuple[int, int]:
        """Безопасно очищает СОДЕРЖИМОЕ директории, не удаляя саму директорию."""
        if not path.is_dir() or not path.exists():
            return 0, 0
            
        logger.debug(f"Очистка содержимого директории: {path}")
        total_deleted_size, error_count = 0, 0
        
        for item in path.iterdir():
            try:
                if item.is_dir():
                    size = self._get_dir_size_safe(item)
                    shutil.rmtree(item)
                    total_deleted_size += size
                elif item.is_file() or item.is_link():
                    size = item.stat().st_size
                    item.unlink()
                    total_deleted_size += size
            except (OSError, PermissionError) as e:
                logger.warning(f"Не удалось удалить '{item}': {e}")
                error_count += 1
                
        return total_deleted_size, error_count

    @staticmethod
    def _get_dir_size_safe(path: Path) -> int:
        """Рекурсивно и безопасно подсчитывает размер директории."""
        total = 0
        try:
            for entry in os.scandir(path):
                if entry.is_dir(follow_symlinks=False):
                    total += SmartCleaner._get_dir_size_safe(Path(entry.path))
                elif entry.is_file(follow_symlinks=False):
                    total += entry.stat().st_size
        except (OSError, FileNotFoundError):
            return 0
        return total

    async def _calculate_dir_size_safe(self, path: Path) -> int:
        """Асинхронная обертка для _get_dir_size_safe."""
        return await asyncio.to_thread(self._get_dir_size_safe, path)