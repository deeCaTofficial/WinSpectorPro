# tests/test_smart_cleaner.py
"""
Тесты очистки. Все операции идут во временном каталоге pytest — тесты
никогда не касаются реальных системных путей.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from winspector.core.modules.smart_cleaner import SmartCleaner


@pytest.fixture
def cleaner(cleanup_rules) -> SmartCleaner:
    return SmartCleaner(cleanup_rules=cleanup_rules)


def make_file(path: Path, size: int = 100) -> Path:
    """Создаёт файл заданного размера."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    return path


# --- Защита путей ----------------------------------------------------------


class TestPathSafety:
    """Ни один путь вне разрешённой зоны не должен пройти проверку."""

    def test_temp_subdirectory_is_allowed(self, cleaner, tmp_path):
        assert cleaner.is_safe_to_delete(tmp_path / "cache")

    @pytest.mark.parametrize(
        "raw",
        [
            "C:\\",
            "C:\\Windows",
            "C:\\Windows\\System32",
            "C:\\Program Files",
            "C:\\ProgramData",
        ],
    )
    def test_system_roots_are_refused(self, cleaner, raw):
        assert not cleaner.is_safe_to_delete(Path(raw))

    def test_user_profile_root_is_refused(self, cleaner):
        profile = os.environ.get("USERPROFILE")
        if not profile:
            pytest.skip("USERPROFILE не задан")
        assert not cleaner.is_safe_to_delete(Path(profile))

    def test_ancestor_of_protected_root_is_refused(self, cleaner):
        """Путь, содержащий внутри себя защищённый каталог, тоже запрещён."""
        profile = os.environ.get("USERPROFILE")
        if not profile:
            pytest.skip("USERPROFILE не задан")
        assert not cleaner.is_safe_to_delete(Path(profile).parent)

    def test_drive_root_variants_refused(self, cleaner):
        assert not cleaner.is_safe_to_delete(Path("C:/"))
        assert not cleaner.is_safe_to_delete(Path("D:\\"))

    @pytest.mark.parametrize("raw", ["C:\\$Recycle.Bin", "C:\\AMD", "C:\\NVIDIA", "C:\\Intel"])
    def test_legitimate_top_level_dirs_are_allowed(self, cleaner, raw):
        """
        Регрессия: запрет всех каталогов верхнего уровня отсекал реальные
        правила базы знаний (корзина, кеши драйверов) — две категории
        очистки молча выпадали из работы.
        """
        assert cleaner.is_safe_to_delete(Path(raw))

    def test_users_root_is_refused_as_profile_ancestor(self, cleaner):
        profile = os.environ.get("USERPROFILE")
        if not profile:
            pytest.skip("USERPROFILE не задан")
        assert not cleaner.is_safe_to_delete(Path(profile).parent)


# --- Удаление содержимого --------------------------------------------------


class TestDirectoryContentCleanup:
    def test_removes_files_and_keeps_the_directory(self, cleaner, tmp_path):
        target = tmp_path / "cache"
        make_file(target / "a.tmp", 100)
        make_file(target / "b.tmp", 200)

        size, count, errors = cleaner._clean_directory_content(target)

        assert size == 300
        assert count == 2
        assert errors == 0
        assert target.is_dir()
        assert not list(target.iterdir())

    def test_removes_nested_directories(self, cleaner, tmp_path):
        target = tmp_path / "cache"
        make_file(target / "sub" / "deep" / "file.bin", 50)

        size, count, _ = cleaner._clean_directory_content(target)

        assert size == 50
        assert count == 1
        assert not list(target.iterdir())

    def test_missing_directory_is_not_an_error(self, cleaner, tmp_path):
        assert cleaner._clean_directory_content(tmp_path / "nope") == (0, 0, 0)

    def test_refuses_unsafe_directory(self, cleaner):
        assert cleaner._clean_directory_content(Path("C:/Windows")) == (0, 0, 0)

    @pytest.mark.skipif(os.name != "nt", reason="поведение специфично для Windows")
    def test_broken_symlink_does_not_crash(self, cleaner, tmp_path):
        """
        Регрессия: старый код вызывал несуществующий `Path.is_link()` и падал
        с AttributeError на всём, что не является обычным файлом.
        """
        target = tmp_path / "cache"
        target.mkdir()
        link = target / "dangling.lnk"
        try:
            link.symlink_to(tmp_path / "missing-target")
        except OSError:
            pytest.skip("Создание симлинков требует прав разработчика")

        _, count, errors = cleaner._clean_directory_content(target)

        assert errors == 0
        assert not link.exists()
        assert count == 1

    @pytest.mark.skipif(os.name != "nt", reason="поведение специфично для Windows")
    def test_symlink_target_outside_is_preserved(self, cleaner, tmp_path):
        """Удаляется сама ссылка, но не то, на что она указывает."""
        outside = make_file(tmp_path / "outside" / "important.txt", 10)
        target = tmp_path / "cache"
        target.mkdir()
        try:
            (target / "link.txt").symlink_to(outside)
        except OSError:
            pytest.skip("Создание симлинков требует прав разработчика")

        cleaner._clean_directory_content(target)

        assert outside.exists(), "Файл вне очищаемого каталога не должен удаляться"


# --- Подсчёт размера -------------------------------------------------------


class TestDirectorySize:
    """Подсчёт размера — самое горячее место приложения (реализация на scandir)."""

    def test_sums_nested_files(self, cleaner, tmp_path):
        make_file(tmp_path / "a.bin", 100)
        make_file(tmp_path / "sub" / "b.bin", 200)
        make_file(tmp_path / "sub" / "deep" / "c.bin", 300)

        assert cleaner._get_dir_size_safe(tmp_path) == 600

    def test_empty_directory_is_zero(self, cleaner, tmp_path):
        (tmp_path / "empty").mkdir()
        assert cleaner._get_dir_size_safe(tmp_path / "empty") == 0

    def test_missing_directory_is_zero(self, cleaner, tmp_path):
        assert cleaner._get_dir_size_safe(tmp_path / "absent") == 0

    def test_deep_tree_does_not_hit_recursion_limit(self, cleaner, tmp_path):
        """Обход итеративный: кеши бывают вложены на сотни уровней."""
        current = tmp_path
        for index in range(60):
            current = current / f"level{index}"
        make_file(current / "deep.bin", 42)

        assert cleaner._get_dir_size_safe(tmp_path) == 42

    @pytest.mark.skipif(os.name != "nt", reason="поведение специфично для Windows")
    def test_symlinked_directory_is_not_counted_twice(self, cleaner, tmp_path):
        real = tmp_path / "real"
        make_file(real / "payload.bin", 1000)
        scanned = tmp_path / "scanned"
        scanned.mkdir()
        try:
            (scanned / "link").symlink_to(real, target_is_directory=True)
        except OSError:
            pytest.skip("Создание симлинков требует прав разработчика")

        assert cleaner._get_dir_size_safe(scanned) == 0


# --- Поиск по маске --------------------------------------------------------


class TestFindFilesByMask:
    def test_matches_only_the_mask(self, cleaner, tmp_path):
        make_file(tmp_path / "a.log", 10)
        make_file(tmp_path / "b.log", 20)
        make_file(tmp_path / "c.txt", 30)

        found = cleaner._find_files_by_mask(str(tmp_path / "*.log"), {})

        assert {p.name for p, _ in found} == {"a.log", "b.log"}

    def test_protected_extensions_are_never_returned(self, cleaner, tmp_path):
        for name in ("app.exe", "lib.dll", "driver.sys", "script.ps1"):
            make_file(tmp_path / name, 10)

        assert cleaner._find_files_by_mask(str(tmp_path / "*.*"), {}) == []

    def test_non_recursive_mask_stays_in_directory(self, cleaner, tmp_path):
        make_file(tmp_path / "top.log", 10)
        make_file(tmp_path / "sub" / "nested.log", 10)

        found = cleaner._find_files_by_mask(str(tmp_path / "*.log"), {})

        assert {p.name for p, _ in found} == {"top.log"}

    def test_recursive_mask_descends(self, cleaner, tmp_path):
        make_file(tmp_path / "top.log", 10)
        make_file(tmp_path / "sub" / "nested.log", 10)

        found = cleaner._find_files_by_mask(str(tmp_path / "**" / "*.log"), {})

        assert {p.name for p, _ in found} == {"top.log", "nested.log"}

    def test_age_filter_keeps_recent_files(self, cleaner, tmp_path):
        make_file(tmp_path / "fresh.log", 10)

        found = cleaner._find_files_by_mask(str(tmp_path / "*.log"), {"age_days": 30})

        assert found == []

    def test_missing_directory_returns_empty(self, cleaner, tmp_path):
        assert cleaner._find_files_by_mask(str(tmp_path / "no" / "*.log"), {}) == []


# --- Глубокая очистка ------------------------------------------------------


class TestDeepCleanup:
    """Пути берутся только из отчёта сканера, ответ ИИ задаёт лишь решение."""

    async def test_cleans_only_approved_categories(self, cleaner, tmp_path):
        approved = tmp_path / "approved"
        rejected = tmp_path / "rejected"
        make_file(approved / "junk.tmp", 500)
        make_file(rejected / "keep.tmp", 500)

        junk_report = {
            "browser_cache": {"folders_to_clean": [str(approved)], "files_to_delete": []},
            "python_pip_cache": {"folders_to_clean": [str(rejected)], "files_to_delete": []},
        }
        decisions = {
            "browser_cache": {"clean": True},
            "python_pip_cache": {"clean": False},
        }

        summary = await cleaner.perform_deep_cleanup(decisions, junk_report)

        assert summary["cleaned_size_bytes"] == 500
        assert not list(approved.iterdir())
        assert (rejected / "keep.tmp").exists()

    async def test_ai_supplied_paths_are_ignored(self, cleaner, tmp_path):
        """
        Ключевая гарантия: даже если ответ модели содержит пути, они не
        используются — источником остаётся отчёт сканера.
        """
        victim = make_file(tmp_path / "user_data" / "thesis.docx", 1000)

        decisions = {
            "browser_cache": {
                "clean": True,
                "files_to_delete": [str(victim)],
                "folders_to_clean": [str(victim.parent)],
            }
        }
        # Сканер этой категории ничего не находил.
        summary = await cleaner.perform_deep_cleanup(decisions, junk_report={})

        assert victim.exists(), "Путь из ответа ИИ не должен приводить к удалению"
        assert summary["cleaned_size_bytes"] == 0

    async def test_unsafe_path_in_scan_report_is_skipped(self, cleaner):
        """Даже отчёт сканера проходит проверку безопасности пути."""
        junk_report = {
            "browser_cache": {
                "files_to_delete": [],
                "folders_to_clean": ["C:\\Windows\\System32"],
            }
        }
        summary = await cleaner.perform_deep_cleanup(
            {"browser_cache": {"clean": True}}, junk_report
        )

        assert summary["errors"] == 1
        assert summary["cleaned_size_bytes"] == 0

    async def test_deletes_individual_files(self, cleaner, tmp_path):
        f1 = make_file(tmp_path / "cache" / "a.tmp", 100)
        f2 = make_file(tmp_path / "cache" / "b.tmp", 150)

        summary = await cleaner.perform_deep_cleanup(
            {"browser_cache": {"clean": True}},
            {"browser_cache": {"files_to_delete": [str(f1), str(f2)], "folders_to_clean": []}},
        )

        assert summary["cleaned_size_bytes"] == 250
        assert not f1.exists() and not f2.exists()

    async def test_missing_file_is_not_counted_as_error(self, cleaner, tmp_path):
        ghost = tmp_path / "cache" / "gone.tmp"
        ghost.parent.mkdir(parents=True)

        summary = await cleaner.perform_deep_cleanup(
            {"browser_cache": {"clean": True}},
            {"browser_cache": {"files_to_delete": [str(ghost)], "folders_to_clean": []}},
        )

        assert summary["errors"] == 0

    async def test_empty_decisions_do_nothing(self, cleaner):
        assert (await cleaner.perform_deep_cleanup({}, {}))["deleted_files_count"] == 0

    async def test_none_arguments_are_tolerated(self, cleaner):
        summary = await cleaner.perform_deep_cleanup(None, None)
        assert summary["cleaned_size_bytes"] == 0

    async def test_exception_in_one_category_does_not_abort(self, cleaner, tmp_path, monkeypatch):
        """
        Регрессия: `zip(*gather(return_exceptions=True))` падал с TypeError,
        как только любая задача возвращала исключение.
        """
        good = tmp_path / "good"
        make_file(good / "junk.tmp", 100)

        original = cleaner._clean_directory_content
        calls = {"n": 0}

        def flaky(path: Path):
            calls["n"] += 1
            if calls["n"] == 1:
                raise OSError("диск недоступен")
            return original(path)

        monkeypatch.setattr(cleaner, "_clean_directory_content", flaky)

        summary = await cleaner.perform_deep_cleanup(
            {"browser_cache": {"clean": True}, "system_temp_files": {"clean": True}},
            {
                "browser_cache": {"folders_to_clean": [str(good)], "files_to_delete": []},
                "system_temp_files": {"folders_to_clean": [str(good)], "files_to_delete": []},
            },
        )

        assert summary["errors"] >= 1
        assert isinstance(summary["cleaned_size_bytes"], int)


# --- Пустые каталоги -------------------------------------------------------


class TestEmptyFolders:
    def test_directory_with_only_thumbs_db_counts_as_empty(self, cleaner, tmp_path):
        target = tmp_path / "gallery"
        make_file(target / "Thumbs.db", 10)
        assert cleaner._is_dir_effectively_empty(target)

    def test_directory_with_content_is_not_empty(self, cleaner, tmp_path):
        target = tmp_path / "gallery"
        make_file(target / "photo.jpg", 10)
        assert not cleaner._is_dir_effectively_empty(target)

    def test_prunes_nested_empty_dirs_upwards(self, cleaner, tmp_path):
        deep = tmp_path / "a" / "b" / "c"
        deep.mkdir(parents=True)

        deleted, _ = cleaner._cleanup_empty_dirs(deep)

        assert deleted >= 1
        assert not deep.exists()

    def test_stops_at_non_empty_parent(self, cleaner, tmp_path):
        deep = tmp_path / "a" / "b"
        deep.mkdir(parents=True)
        make_file(tmp_path / "a" / "keep.txt", 10)

        cleaner._cleanup_empty_dirs(deep)

        assert (tmp_path / "a").exists()
        assert (tmp_path / "a" / "keep.txt").exists()

    def test_protected_folder_name_is_kept(self, cleaner, tmp_path):
        protected = tmp_path / "SendTo"
        protected.mkdir()
        deleted, _ = cleaner._cleanup_empty_dirs(protected)
        assert deleted == 0
        assert protected.exists()

    async def test_cleanup_all_empty_folders_reports_shape(self, cleaner):
        summary = await cleaner.cleanup_all_empty_folders_async(extra_paths=[])
        assert set(summary) == {"deleted_folders_count", "errors"}
        assert isinstance(summary["deleted_folders_count"], int)


# --- Сканирование ----------------------------------------------------------


class TestScanning:
    async def test_scan_rule_reports_directory_size(self, cleaner, tmp_path):
        make_file(tmp_path / "cache" / "a.bin", 400)

        report = await cleaner._scan_rule("browser_cache", {"paths": [str(tmp_path / "cache")]})

        assert report["category_id"] == "browser_cache"
        assert report["total_size"] == 400
        assert report["folders_to_clean"] == [str(tmp_path / "cache")]

    async def test_scan_rule_strips_provenance(self, cleaner, tmp_path):
        """Служебные поля не должны попадать в промпт."""
        report = await cleaner._scan_rule(
            "browser_cache",
            {"paths": [str(tmp_path)], "provenance": {"added_by": "ai", "comment": "x" * 500}},
        )
        assert "provenance" not in report

    async def test_scan_rule_survives_missing_paths(self, cleaner, tmp_path):
        report = await cleaner._scan_rule("browser_cache", {"paths": [str(tmp_path / "absent")]})
        assert report["total_size"] == 0

    async def test_find_junk_files_deep_skips_empty_categories(self, tmp_path):
        cleaner = SmartCleaner(
            cleanup_rules=[
                {"category_id": "empty_one", "paths": [str(tmp_path / "nothing")]},
            ]
        )
        assert await cleaner.find_junk_files_deep() == {}

    def test_rules_without_category_id_are_ignored(self):
        cleaner = SmartCleaner(cleanup_rules=[{"paths": ["%TEMP%"]}, None, "junk"])
        assert cleaner.rules == {}


# --- Стандартная очистка ---------------------------------------------------


class TestStandardCleanup:
    """
    Стандартная очистка работает по зашитому в код списку. Тесты подменяют
    пути на временные, чтобы не трогать настоящий %TEMP% машины.
    """

    @pytest.fixture
    def sandboxed(self, cleaner, tmp_path, monkeypatch):
        """Перенаправляет весь стандартный план во временный каталог."""
        sandbox = tmp_path / "sandbox"
        sandbox.mkdir()

        monkeypatch.setattr(
            SmartCleaner,
            "STANDARD_PLAN",
            {
                "temp_files": {"type": "folder_content", "paths": [str(sandbox / "temp")]},
                "dumps": {"type": "files_by_mask", "paths": [str(sandbox / "dumps" / "*.dmp")]},
                "flush": {"type": "command", "command": ["cmd", "/c", "exit", "0"]},
            },
        )
        return cleaner, sandbox

    async def test_cleans_folders_and_masked_files(self, sandboxed):
        cleaner, sandbox = sandboxed
        make_file(sandbox / "temp" / "old.tmp", 300)
        make_file(sandbox / "dumps" / "crash.dmp", 200)
        keeper = make_file(sandbox / "dumps" / "notes.txt", 50)

        summary = await cleaner.perform_standard_cleanup()

        assert summary["cleaned_size_bytes"] == 500
        assert not (sandbox / "temp" / "old.tmp").exists()
        assert not (sandbox / "dumps" / "crash.dmp").exists()
        assert keeper.exists(), "файл, не подходящий под маску, должен остаться"

    async def test_missing_paths_are_not_errors(self, sandboxed):
        cleaner, _ = sandboxed
        summary = await cleaner.perform_standard_cleanup()
        assert summary["errors"] == 0
        assert summary["cleaned_size_bytes"] == 0

    async def test_failing_command_is_counted(self, cleaner, monkeypatch, tmp_path):
        monkeypatch.setattr(
            SmartCleaner,
            "STANDARD_PLAN",
            {"broken": {"type": "command", "command": ["definitely-not-a-real-binary"]}},
        )
        summary = await cleaner.perform_standard_cleanup()
        assert summary["errors"] == 1

    async def test_summary_shape_is_stable(self, sandboxed):
        cleaner, _ = sandboxed
        summary = await cleaner.perform_standard_cleanup()
        assert set(summary) == {"cleaned_size_bytes", "deleted_files_count", "errors"}
        assert all(isinstance(v, int) for v in summary.values())

    async def test_real_plan_targets_only_expected_locations(self, cleaner):
        """
        Проверка данных: боевой план не должен ссылаться на пользовательские
        документы или корень диска.
        """
        forbidden = ("%USERPROFILE%\\Documents", "%USERPROFILE%\\Desktop", "C:\\")
        for details in SmartCleaner.STANDARD_PLAN.values():
            for raw_path in details.get("paths", []):
                assert not any(raw_path.startswith(bad) for bad in forbidden), raw_path
