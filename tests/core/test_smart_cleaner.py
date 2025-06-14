# tests/core/test_smart_cleaner.py
"""
Тесты для модуля интеллектуальной очистки SmartCleaner.
"""
import pytest
import os
import time
from pathlib import Path

from src.winspector.core.modules.smart_cleaner import SmartCleaner

# Помечаем все тесты в этом файле как асинхронные
pytestmark = pytest.mark.asyncio

# Определяем тестовую базу знаний для очистки
TEST_KNOWLEDGE_BASE = {
    "heuristic_rules": {
        "old_logs": {
            "heuristic": True, # Указываем, что это эвристический поиск
            "search_path": "%USERPROFILE%\\Documents\\AppLogs", # Уточняем путь
            "extensions": [".log", ".log1"],
            "age_days": 30
        },
        "temp_files": {
            "heuristic": True,
            "search_path": "%USERPROFILE%\\AppData\\Cache",
            "extensions": [".tmp", ".temp"],
            "age_days": 0 # Любого возраста
        }
    }
}


@pytest.fixture
def temp_file_structure(tmp_path: Path) -> Path:
    """
    Фикстура, которая создает сложную временную структуру папок и файлов
    для тестирования поиска и очистки.
    """
    # Создаем структуру папок
    user_profile = tmp_path / "UserProfile"
    
    cache_dir = user_profile / "AppData" / "Cache"
    cache_dir.mkdir(parents=True)
    
    logs_dir = user_profile / "Documents" / "AppLogs"
    logs_dir.mkdir(parents=True)

    # Создаем файлы
    # 1. Старые логи, которые должны быть найдены
    old_log_path = logs_dir / "app_2023.log"
    old_log_path.write_text("old log content")
    forty_days_ago = time.time() - 40 * 86400
    os.utime(old_log_path, (forty_days_ago, forty_days_ago))

    # 2. Новые логи, которые НЕ должны быть найдены
    (logs_dir / "app_today.log").write_text("new log")

    # 3. Временные файлы, которые должны быть найдены
    (cache_dir / "session.tmp").write_text("temp content")

    # 4. Обычный файл, который НЕ должен быть затронут
    (user_profile / "Documents" / "my_document.txt").write_text("important data")
    
    # Мокаем системную переменную, чтобы модуль искал файлы в нашей временной папке
    os.environ["USERPROFILE"] = str(user_profile)
    
    return user_profile


class TestSmartCleaner:
    """Группа тестов для класса SmartCleaner."""

    def test_initialization_with_rules(self):
        """Проверяет, что SmartCleaner корректно инициализируется с правилами."""
        # GIVEN
        rules = TEST_KNOWLEDGE_BASE["heuristic_rules"]
        
        # WHEN
        # ИСПРАВЛЕНИЕ: Используем правильное имя аргумента `heuristic_rules`
        cleaner = SmartCleaner(heuristic_rules=rules)
        
        # THEN
        assert "old_logs" in cleaner.rules # Теперь правила хранятся в self.rules
        assert cleaner.rules["old_logs"]["age_days"] == 30

    async def test_find_junk_files_deep_finds_correct_files(self, temp_file_structure):
        """
        Проверяет, что эвристический поиск находит только те файлы,
        которые соответствуют правилам из базы знаний.
        """
        # GIVEN
        rules = TEST_KNOWLEDGE_BASE["heuristic_rules"]
        cleaner = SmartCleaner(heuristic_rules=rules)
        
        # WHEN
        junk_report = await cleaner.find_junk_files_deep()

        # THEN
        assert "old_logs" in junk_report
        assert "temp_files" in junk_report
        
        old_logs_data = junk_report["old_logs"]
        assert old_logs_data["count"] == 1
        assert old_logs_data["size"] == len("old log content")
        assert "app_2023.log" in str(old_logs_data["files_to_delete"][0])
        
        temp_files_data = junk_report["temp_files"]
        assert temp_files_data["count"] == 1
        
        assert len(junk_report) == 2

    async def test_perform_deep_cleanup_deletes_correct_files(self, temp_file_structure):
        """
        Проверяет, что очистка по плану удаляет нужные файлы, не трогая остальные.
        """
        # GIVEN
        rules = TEST_KNOWLEDGE_BASE["heuristic_rules"]
        cleaner = SmartCleaner(heuristic_rules=rules)
        initial_report = await cleaner.find_junk_files_deep()
        cleanup_plan = {category: {"clean": True, **details} for category, details in initial_report.items()}
        
        # WHEN
        cleanup_summary = await cleaner.perform_deep_cleanup(cleanup_plan)

        # THEN
        assert not (temp_file_structure / "Documents" / "AppLogs" / "app_2023.log").exists()
        assert not (temp_file_structure / "AppData" / "Cache" / "session.tmp").exists()
        
        assert (temp_file_structure / "Documents" / "AppLogs" / "app_today.log").exists()
        assert (temp_file_structure / "Documents" / "my_document.txt").exists()
        
        assert cleanup_summary["deleted_files_count"] == 2
        assert cleanup_summary["cleaned_size_bytes"] == len("old log content") + len("temp content")
        assert cleanup_summary["errors"] == 0

    async def test_perform_deep_cleanup_handles_permission_error(self, temp_file_structure, mocker):
        """
        Проверяет, что очистка не падает при ошибке удаления файла,
        а корректно обрабатывает ее и логирует.
        """
        # GIVEN
        rules = TEST_KNOWLEDGE_BASE["heuristic_rules"]
        cleaner = SmartCleaner(heuristic_rules=rules)
        initial_report = await cleaner.find_junk_files_deep()
        cleanup_plan = {category: {"clean": True, **details} for category, details in initial_report.items()}
        
        # Мокаем os.remove, чтобы он вызывал ошибку
        mocker.patch("os.remove", side_effect=PermissionError("Access Denied"))
        # ИСПРАВЛЕНИЕ: Мокаем os.rmdir для директорий, если они будут удаляться
        mocker.patch("shutil.rmtree", side_effect=PermissionError("Access Denied"))
        mock_logger_info = mocker.patch("src.winspector.core.modules.smart_cleaner.logger.info")

        # WHEN
        cleanup_summary = await cleaner.perform_deep_cleanup(cleanup_plan)

        # THEN
        assert cleanup_summary["deleted_files_count"] == 0
        assert cleanup_summary["cleaned_size_bytes"] == 0
        assert cleanup_summary["errors"] > 0
        
        assert "Очистка категории: old_logs" in str(mock_logger_info.call_args_list)
        assert "Очистка категории: temp_files" in str(mock_logger_info.call_args_list)