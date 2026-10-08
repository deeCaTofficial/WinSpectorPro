# tests/test_smart_cleaner.py
"""
Тесты очистки. Все операции идут во временном каталоге pytest — тесты
никогда не касаются реальных системных путей.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from winspector.core.modules.smart_cleaner import CleanupSummary, SmartCleaner


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

    @pytest.mark.parametrize(
        "relative",
        ["Downloads", "Documents", r"Downloads\nested\deeper", r"Pictures\2024", r"source\repos"],
    )
    def test_user_data_subtrees_are_refused_entirely(self, cleaner, relative):
        """
        Папки с данными пользователя закрыты вместе со всем содержимым:
        правило «очистить папку загрузок» не должно исполняться ни ИИ, ни
        офлайн-планировщиком, что бы ни стояло в базе знаний.
        """
        profile = os.environ.get("USERPROFILE")
        if not profile:
            pytest.skip("USERPROFILE не задан")
        assert not cleaner.is_safe_to_delete(Path(profile) / relative)

    def test_jump_lists_and_quarantine_are_refused(self, cleaner):
        appdata = os.environ.get("APPDATA")
        program_data = os.environ.get("ProgramData", r"C:\ProgramData")
        if not appdata:
            pytest.skip("APPDATA не задан")
        recent = Path(appdata) / "Microsoft" / "Windows" / "Recent"
        assert not cleaner.is_safe_to_delete(recent / "AutomaticDestinations")
        assert not cleaner.is_safe_to_delete(recent / "CustomDestinations")
        quarantine = Path(program_data) / "Microsoft" / "Windows Defender" / "Quarantine"
        assert not cleaner.is_safe_to_delete(quarantine / "Entries")

    @pytest.mark.parametrize("relative", [r"HexChat\logs", r"mIRC\logs", r"Thunderbird\Profiles"])
    def test_chat_history_is_refused(self, cleaner, relative):
        """`logs` у IRC- и почтовых клиентов — переписка, а не диагностика."""
        appdata = os.environ.get("APPDATA")
        if not appdata:
            pytest.skip("APPDATA не задан")
        assert not cleaner.is_safe_to_delete(Path(appdata) / relative)

    def test_temp_inside_profile_is_still_allowed(self, cleaner):
        """Защита поддеревьев не должна задеть законные цели рядом с ними."""
        local = os.environ.get("LOCALAPPDATA")
        if not local:
            pytest.skip("LOCALAPPDATA не задан")
        assert cleaner.is_safe_to_delete(Path(local) / "Temp")
        assert cleaner.is_safe_to_delete(Path(local) / "NVIDIA" / "DXCache")


# --- Удаление содержимого --------------------------------------------------


class TestDirectoryContentCleanup:
    def test_removes_files_and_keeps_the_directory(self, cleaner, tmp_path):
        target = tmp_path / "cache"
        make_file(target / "a.tmp", 100)
        make_file(target / "b.tmp", 200)

        result = cleaner._clean_directory_content(target)

        assert result.sizes["deleted"] == 300
        assert result.counts["deleted"] == 2
        assert result.errors == 0
        assert target.is_dir()
        assert not list(target.iterdir())

    def test_removes_nested_directories(self, cleaner, tmp_path):
        target = tmp_path / "cache"
        make_file(target / "sub" / "deep" / "file.bin", 50)

        result = cleaner._clean_directory_content(target)

        assert result.sizes["deleted"] == 50
        assert result.counts["deleted"] == 1
        # Опустевшие `sub/deep` и `sub` убраны после файлов, сам `cache` остался.
        assert result.dirs_removed == 2
        assert not list(target.iterdir())

    def test_missing_directory_is_not_an_error(self, cleaner, tmp_path):
        result = cleaner._clean_directory_content(tmp_path / "nope")
        assert result.errors == 0 and result.counts["deleted"] == 0

    def test_refuses_unsafe_directory(self, cleaner):
        result = cleaner._clean_directory_content(Path("C:/Windows"))
        assert result.counts["deleted"] == 0 and result.dirs_removed == 0

    def test_files_first_then_only_emptied_directories(self, cleaner, tmp_path):
        """
        Правило очистки: сначала удаляются файлы, потом проверяются каталоги.
        Каталог с занятым файлом должен уцелеть вместе со всей цепочкой
        родителей, а его пустые соседи — удалиться.
        """
        target = tmp_path / "cache"
        busy = make_file(target / "held" / "inner" / "busy.bin", 30)
        make_file(target / "held" / "inner" / "free.bin", 20)
        make_file(target / "gone" / "a.tmp", 10)

        with busy.open("rb"):
            result = cleaner._clean_directory_content(target)

        assert result.counts["deleted"] == 2
        assert result.counts["locked"] == 1
        assert result.sizes["locked"] == 30
        assert busy.exists()
        assert (target / "held" / "inner").is_dir(), "каталог занятого файла остаётся"
        assert not (target / "gone").exists(), "опустевший каталог удалён"
        assert result.dirs_removed == 1

    def test_recent_files_are_kept_when_min_age_is_set(self, cleaner, tmp_path):
        target = tmp_path / "temp"
        old = make_file(target / "old.tmp", 100)
        fresh = make_file(target / "fresh.tmp", 50)
        os.utime(old, (time.time() - 3 * 86400,) * 2)

        result = cleaner._clean_directory_content(target, min_age_seconds=86400)

        assert not old.exists()
        assert fresh.exists()
        assert result.counts["too_recent"] == 1
        assert result.sizes["deleted"] == 100

    @pytest.mark.skipif(os.name != "nt", reason="junction-точки есть только в Windows")
    def test_junction_is_removed_as_link_and_never_followed(self, cleaner, tmp_path):
        """
        `os.walk` и `DirEntry.is_dir(follow_symlinks=False)` раскрывают
        junction-точки. Очистка обязана снять саму точку, не заходя внутрь.
        """
        import _winapi

        outside = make_file(tmp_path / "outside" / "keep.txt", 10)
        target = tmp_path / "cache"
        target.mkdir()
        _winapi.CreateJunction(str(outside.parent), str(target / "junc"))

        result = cleaner._clean_directory_content(target)

        assert outside.exists(), "содержимое цели junction нетронуто"
        assert not (target / "junc").exists(), "сама junction-точка снята"
        assert result.counts["deleted"] == 1

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

        result = cleaner._clean_directory_content(target)

        assert result.errors == 0
        assert not link.exists()
        assert result.counts["deleted"] == 1

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

    def test_size_respects_min_age(self, cleaner, tmp_path):
        old = make_file(tmp_path / "old.bin", 700)
        make_file(tmp_path / "fresh.bin", 300)
        os.utime(old, (time.time() - 2 * 86400,) * 2)

        assert cleaner._get_dir_size_safe(tmp_path, min_age_seconds=3600) == 700
        assert cleaner._get_dir_size_safe(tmp_path) == 1000

    @pytest.mark.skipif(os.name != "nt", reason="junction-точки есть только в Windows")
    def test_junction_target_is_not_counted(self, cleaner, tmp_path):
        import _winapi

        real = make_file(tmp_path / "real" / "payload.bin", 1000)
        scanned = tmp_path / "scanned"
        scanned.mkdir()
        _winapi.CreateJunction(str(real.parent), str(scanned / "junc"))

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

    async def test_category_is_skipped_while_its_program_runs(self, tmp_path, monkeypatch):
        """
        Кеш работающей программы не трогается, а причина попадает в отчёт —
        пользователь видит «пропущено: запущен msedge.exe», а не тишину.
        """
        cleaner = SmartCleaner(
            [
                {
                    "category_id": "edge_cache",
                    "paths": [str(tmp_path / "edge")],
                    "requires_closed": ["msedge.exe"],
                },
                {"category_id": "pip_cache", "paths": [str(tmp_path / "pip")]},
            ]
        )
        edge_file = make_file(tmp_path / "edge" / "data_0", 100)
        pip_file = make_file(tmp_path / "pip" / "wheel.whl", 200)
        monkeypatch.setattr(cleaner, "_running_process_names", lambda: {"msedge.exe", "code.exe"})

        summary = await cleaner.perform_deep_cleanup(
            {"edge_cache": {"clean": True}, "pip_cache": {"clean": True}},
            {
                "edge_cache": {"folders_to_clean": [str(tmp_path / "edge")], "files_to_delete": []},
                "pip_cache": {"folders_to_clean": [str(tmp_path / "pip")], "files_to_delete": []},
            },
        )

        assert edge_file.exists()
        assert not pip_file.exists()
        assert summary["skipped_categories"] == {"edge_cache": "запущено: msedge.exe"}
        assert summary["cleaned_size_bytes"] == 200

    async def test_category_is_cleaned_when_program_is_closed(self, tmp_path, monkeypatch):
        cleaner = SmartCleaner(
            [
                {
                    "category_id": "edge_cache",
                    "paths": [str(tmp_path / "edge")],
                    "requires_closed": ["msedge.exe"],
                }
            ]
        )
        edge_file = make_file(tmp_path / "edge" / "data_0", 100)
        monkeypatch.setattr(cleaner, "_running_process_names", lambda: {"explorer.exe"})

        summary = await cleaner.perform_deep_cleanup(
            {"edge_cache": {"clean": True}},
            {"edge_cache": {"folders_to_clean": [str(tmp_path / "edge")], "files_to_delete": []}},
        )

        assert not edge_file.exists()
        assert summary["skipped_categories"] == {}

    async def test_locked_files_are_reported_as_skipped_not_freed(self, cleaner, tmp_path):
        """Честный учёт: занятый файл не попадает в «освобождено»."""
        busy = make_file(tmp_path / "cache" / "busy.bin", 300)
        free = make_file(tmp_path / "cache" / "free.bin", 100)

        with busy.open("rb"):
            summary = await cleaner.perform_deep_cleanup(
                {"browser_cache": {"clean": True}},
                {
                    "browser_cache": {
                        "files_to_delete": [str(busy), str(free)],
                        "folders_to_clean": [],
                    }
                },
            )

        assert summary["cleaned_size_bytes"] == 100
        assert summary["deleted_files_count"] == 1
        assert summary["skipped_files_count"] == 1
        assert summary["skipped_size_bytes"] == 300
        assert summary["errors"] == 0

    async def test_rule_min_age_applies_to_folders(self, tmp_path):
        cleaner = SmartCleaner(
            [{"category_id": "temp", "paths": [str(tmp_path / "t")], "min_age_hours": 24}]
        )
        old = make_file(tmp_path / "t" / "old.tmp", 10)
        fresh = make_file(tmp_path / "t" / "fresh.tmp", 10)
        os.utime(old, (time.time() - 2 * 86400,) * 2)

        report = await cleaner._scan_rule("temp", cleaner.rules["temp"])
        assert report["total_size"] == 10, "сканер тоже не считает свежие файлы"

        await cleaner.perform_deep_cleanup(
            {"temp": {"clean": True}},
            {"temp": {"folders_to_clean": [str(tmp_path / "t")], "files_to_delete": []}},
        )
        assert not old.exists() and fresh.exists()

    async def test_exception_in_one_category_does_not_abort(self, cleaner, tmp_path, monkeypatch):
        """
        Регрессия: `zip(*gather(return_exceptions=True))` падал с TypeError,
        как только любая задача возвращала исключение.
        """
        good = tmp_path / "good"
        make_file(good / "junk.tmp", 100)

        original = cleaner._clean_directory_content
        calls = {"n": 0}

        def flaky(path: Path, *args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise OSError("диск недоступен")
            return original(path, *args, **kwargs)

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

    @pytest.fixture
    def isolated_temp(self, tmp_path, monkeypatch) -> Path:
        """
        Подменяет %TEMP% и %WINDIR%\\Temp каталогами pytest.

        Без этого тест прошёлся бы по настоящему %TEMP% разработчика — и
        удалял там пустые каталоги (десятки тысяч, полминуты) при каждом
        прогоне, в том числе из pre-commit.
        """
        temp = tmp_path / "temp"
        windir = tmp_path / "windir"
        (windir / "Temp").mkdir(parents=True)
        temp.mkdir()
        monkeypatch.setenv("TEMP", str(temp))
        monkeypatch.setenv("WINDIR", str(windir))
        return temp

    async def test_cleanup_all_empty_folders_reports_shape(self, cleaner, isolated_temp):
        summary = await cleaner.cleanup_all_empty_folders_async(extra_paths=[])
        assert set(summary) == {"deleted_folders_count", "errors", "app_dirs_removed"}
        assert isinstance(summary["deleted_folders_count"], int)

    async def test_cleanup_removes_nested_empty_dirs_but_keeps_root(self, cleaner, isolated_temp):
        (isolated_temp / "a" / "b" / "c").mkdir(parents=True)
        make_file(isolated_temp / "a" / "b" / "c" / "Thumbs.db", 1)
        (isolated_temp / "busy").mkdir()
        make_file(isolated_temp / "busy" / "keep.txt", 1)

        summary = await cleaner.cleanup_all_empty_folders_async(extra_paths=[])

        assert summary["deleted_folders_count"] == 3  # c, b, a — снизу вверх
        assert isolated_temp.is_dir(), "корень %TEMP% не удаляется"
        assert not (isolated_temp / "a").exists()
        assert (isolated_temp / "busy" / "keep.txt").exists()

    def test_effectively_empty_dir_is_removed_without_rmtree(self, cleaner, tmp_path):
        target = tmp_path / "gallery"
        make_file(target / "Thumbs.db", 10)

        deleted, errors = cleaner._process_empty_folder_cleanup(tmp_path)

        assert (deleted, errors) == (1, 0)
        assert not target.exists()

    @pytest.mark.skipif(os.name != "nt", reason="junction-точки есть только в Windows")
    def test_empty_folder_pass_does_not_enter_junctions(self, cleaner, tmp_path):
        """
        Регрессия: `os.walk` заходит внутрь junction-точек, и «пустые» каталоги
        за пределами %TEMP% удалялись бы вместе с ними.
        """
        import _winapi

        outside = tmp_path / "outside"
        (outside / "empty_far_away").mkdir(parents=True)
        temp = tmp_path / "temp"
        temp.mkdir()
        _winapi.CreateJunction(str(outside), str(temp / "junc"))

        cleaner._process_empty_folder_cleanup(temp)

        assert (outside / "empty_far_away").is_dir(), "каталог за junction нетронут"

    async def test_empty_app_dir_is_removed_with_its_empty_subdirs(self, cleaner, isolated_temp):
        app = isolated_temp.parent / "Program Files" / "Hypixel Studios"
        (app / "Hytale" / "logs").mkdir(parents=True)

        summary = await cleaner.cleanup_all_empty_folders_async(extra_paths=[], app_dirs=[str(app)])

        assert not app.exists()
        assert app.parent.is_dir(), "корень Program Files не трогается"
        assert summary["app_dirs_removed"] == [str(app)]
        assert summary["deleted_folders_count"] == 3  # logs, Hytale, Hypixel Studios

    async def test_app_dir_that_got_a_file_is_left_whole(self, cleaner, isolated_temp):
        """Программу поставили заново между поиском и очисткой — ничего не трогаем."""
        app = isolated_temp.parent / "Program Files" / "Reinstalled"
        (app / "empty_sub").mkdir(parents=True)
        make_file(app / "bin" / "app.exe", 10)

        summary = await cleaner.cleanup_all_empty_folders_async(extra_paths=[], app_dirs=[str(app)])

        assert (app / "empty_sub").is_dir(), "даже пустой подкаталог не удалён"
        assert (app / "bin" / "app.exe").exists()
        assert summary["app_dirs_removed"] == []

    async def test_protected_app_dir_is_not_removed(self, cleaner, isolated_temp, monkeypatch):
        app = isolated_temp.parent / "Guarded"
        app.mkdir()
        monkeypatch.setattr(cleaner, "is_safe_to_delete", lambda _path: False)

        summary = await cleaner.cleanup_all_empty_folders_async(extra_paths=[], app_dirs=[str(app)])

        assert app.is_dir()
        assert summary["app_dirs_removed"] == []

    async def test_protected_folder_names_survive_cleanup(self, cleaner, isolated_temp):
        (isolated_temp / "SendTo").mkdir()
        (isolated_temp / "plain").mkdir()

        await cleaner.cleanup_all_empty_folders_async(extra_paths=[])

        assert (isolated_temp / "SendTo").is_dir()
        assert not (isolated_temp / "plain").exists()


# --- Сканирование ----------------------------------------------------------


class TestRulePathExpansion:
    """`*` в правиле для каталогов подставляет каталог, в правиле для файлов — маску."""

    def test_directory_glob_expands_to_matching_directories(self, cleaner, tmp_path):
        make_file(tmp_path / "User Data" / "Default" / "Cache" / "data_0")
        make_file(tmp_path / "User Data" / "Profile 1" / "Cache" / "data_0")
        make_file(tmp_path / "User Data" / "Profile 1" / "Bookmarks", 5)

        expanded = cleaner._expand_rule_paths(
            {"cleanup_type": "folder", "paths": [str(tmp_path / "User Data" / "*" / "Cache")]}
        )

        assert [Path(p).parent.name for p, _ in expanded] == ["Default", "Profile 1"]
        assert all(not is_mask for _, is_mask in expanded)

    def test_file_rule_keeps_mask_semantics(self, cleaner, tmp_path):
        expanded = cleaner._expand_rule_paths(
            {"cleanup_type": "files", "paths": [str(tmp_path / "*.log")]}
        )
        assert expanded == [(str(tmp_path / "*.log"), True)]

    def test_plain_paths_pass_through(self, cleaner, tmp_path):
        expanded = cleaner._expand_rule_paths({"paths": [str(tmp_path / "absent")]})
        assert expanded == [(str(tmp_path / "absent"), False)]

    @pytest.mark.skipif(os.name != "nt", reason="junction-точки есть только в Windows")
    def test_junction_matches_are_dropped(self, cleaner, tmp_path):
        import _winapi

        outside = make_file(tmp_path / "outside" / "keep.txt")
        (tmp_path / "apps" / "real" / "Cache").mkdir(parents=True)
        (tmp_path / "apps" / "linked").mkdir()
        _winapi.CreateJunction(str(outside.parent), str(tmp_path / "apps" / "linked" / "Cache"))

        expanded = cleaner._expand_rule_paths({"paths": [str(tmp_path / "apps" / "*" / "Cache")]})

        assert [Path(p).parent.name for p, _ in expanded] == ["real"]

    async def test_scan_rule_sizes_every_expanded_directory(self, cleaner, tmp_path):
        make_file(tmp_path / "games" / "A" / "Saved" / "Logs" / "a.log", 100)
        make_file(tmp_path / "games" / "B" / "Saved" / "Logs" / "b.log", 200)
        make_file(tmp_path / "games" / "B" / "Saved" / "SaveGames" / "slot1.sav", 999)

        report = await cleaner._scan_rule(
            "ue_logs", {"paths": [str(tmp_path / "games" / "*" / "Saved" / "Logs")]}
        )

        assert report["total_size"] == 300
        assert len(report["folders_to_clean"]) == 2
        assert all(p.endswith("Logs") for p in report["folders_to_clean"])


class TestOverlappingRules:
    async def test_folder_belongs_to_first_rule_that_found_it(self, tmp_path):
        """Общее правило не считает повторно и не обходит ограничения частного."""
        make_file(tmp_path / "App" / "Code Cache" / "js" / "a_0", 100)
        make_file(tmp_path / "Temp" / "session" / "Code Cache" / "b_0", 50)
        cleaner = SmartCleaner(
            [
                {"category_id": "app_cache", "paths": [str(tmp_path / "App" / "Code Cache")]},
                {"category_id": "temp", "paths": [str(tmp_path / "Temp")]},
                {
                    "category_id": "generic",
                    "paths": [
                        str(tmp_path / "*" / "Code Cache"),
                        str(tmp_path / "*" / "*" / "Code Cache"),
                    ],
                },
            ]
        )

        report = await cleaner.find_junk_files_deep()

        assert report["app_cache"]["total_size"] == 100
        assert report["temp"]["total_size"] == 50
        assert "generic" not in report, "всё найденное уже принадлежит частным правилам"
        assert "_folder_sizes" not in report["app_cache"], "служебные поля не уходят в отчёт"

    def test_exclude_under_drops_matches(self, cleaner, tmp_path):
        (tmp_path / "Packages" / "X" / "Code Cache").mkdir(parents=True)
        (tmp_path / "App" / "Code Cache").mkdir(parents=True)

        expanded = cleaner._expand_rule_paths(
            {
                "paths": [
                    str(tmp_path / "*" / "Code Cache"),
                    str(tmp_path / "*" / "*" / "Code Cache"),
                ],
                "exclude_under": [str(tmp_path / "Packages")],
            }
        )

        assert [Path(p).parent.name for p, _ in expanded] == ["App"]


class TestBusyCaches:
    async def test_cache_of_running_app_is_skipped(self, tmp_path):
        """Имя процесса неизвестно — признак работы программы: занят GPUCache рядом."""
        cache = make_file(tmp_path / "App" / "Code Cache" / "js" / "a_0", 100)
        gpu = make_file(tmp_path / "App" / "GPUCache" / "data_0", 10)
        rule = {
            "category_id": "electron",
            "paths": [str(tmp_path / "App" / "Code Cache")],
            "skip_if_busy": True,
            "busy_siblings": ["GPUCache"],
        }
        cleaner = SmartCleaner([rule])
        report = {
            "electron": {"folders_to_clean": [str(cache.parent.parent)], "files_to_delete": []}
        }

        with gpu.open("rb"):
            summary = await cleaner.perform_deep_cleanup({"electron": {"clean": True}}, report)

        assert cache.exists()
        assert summary["cleaned_size_bytes"] == 0
        assert summary["skipped_categories"] == {
            "electron: App": "программа запущена — её кеш занят"
        }
        assert summary["deferred"] == [{"processes": [], "folder": "App", "size_bytes": 0}]

    async def test_cache_data_uses_profile_level_siblings(self, tmp_path):
        """Для `Cache\\Cache_Data` соседи ищутся на уровне профиля, а не внутри `Cache`."""
        data = make_file(tmp_path / "App" / "Cache" / "Cache_Data" / "f_000001", 100)
        gpu = make_file(tmp_path / "App" / "GPUCache" / "data_0", 10)
        rule = {"busy_siblings": ["GPUCache"]}
        with gpu.open("rb"):
            assert SmartCleaner._busy_file(data.parent, rule) == str(gpu)

    async def test_cache_of_closed_app_is_cleaned(self, tmp_path):
        cache = make_file(tmp_path / "App" / "Code Cache" / "js" / "a_0", 100)
        make_file(tmp_path / "App" / "GPUCache" / "data_0", 10)
        rule = {
            "category_id": "electron",
            "paths": [str(tmp_path / "App" / "Code Cache")],
            "skip_if_busy": True,
            "busy_siblings": ["GPUCache"],
        }
        cleaner = SmartCleaner([rule])
        report = {
            "electron": {"folders_to_clean": [str(cache.parent.parent)], "files_to_delete": []}
        }

        summary = await cleaner.perform_deep_cleanup({"electron": {"clean": True}}, report)

        assert not cache.exists()
        assert summary["cleaned_size_bytes"] == 100


class TestStandardPlanAges:
    def test_recent_memory_dump_is_kept(self, cleaner, tmp_path):
        dump = make_file(tmp_path / "MEMORY.DMP", 1000)
        assert cleaner._single_file_if_old(dump, {"age_days": 7}) == []

    def test_old_memory_dump_is_eligible(self, cleaner, tmp_path):
        dump = make_file(tmp_path / "MEMORY.DMP", 1000)
        stamp = time.time() - 10 * 86400
        os.utime(dump, (stamp, stamp))
        assert cleaner._single_file_if_old(dump, {"age_days": 7}) == [(dump, 1000)]

    def test_temp_in_standard_plan_is_atomic(self):
        assert SmartCleaner.STANDARD_PLAN["temp_files"]["atomic_subdirs"] is True


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

    async def test_offline_scan_skips_non_high_rules(self, tmp_path):
        make_file(tmp_path / "high" / "a.tmp", 10)
        make_file(tmp_path / "medium" / "b.tmp", 10)
        cleaner = SmartCleaner(
            [
                {"category_id": "h", "safety": "high", "paths": [str(tmp_path / "high")]},
                {"category_id": "m", "safety": "medium", "paths": [str(tmp_path / "medium")]},
            ]
        )

        assert set(await cleaner.find_junk_files_deep({"high"})) == {"h"}
        assert set(await cleaner.find_junk_files_deep()) == {"h", "m"}

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
        assert set(summary) == {*CleanupSummary.COUNTERS, "skipped_categories", "deferred"}
        assert all(isinstance(summary[key], int) for key in CleanupSummary.COUNTERS)
        assert summary["skipped_categories"] == {}

    async def test_real_plan_targets_only_expected_locations(self, cleaner):
        """
        Проверка данных: боевой план не должен ссылаться на пользовательские
        документы или корень диска.
        """
        forbidden = ("%USERPROFILE%\\Documents", "%USERPROFILE%\\Desktop", "C:\\")
        for details in SmartCleaner.STANDARD_PLAN.values():
            for raw_path in details.get("paths", []):
                assert not any(raw_path.startswith(bad) for bad in forbidden), raw_path


class TestSurveyFindings:
    """Поправки по итогам обследования реальной машины после очистки."""

    def test_empty_thumbnail_databases_are_not_churned(self, cleaner, tmp_path):
        make_file(tmp_path / "thumbcache_32.db", 70 * 1024)
        big = make_file(tmp_path / "thumbcache_256.db", 3 * 1024 * 1024)

        found = cleaner._find_files_by_mask(
            str(tmp_path / "thumbcache_*.db"), {"min_file_bytes": 1024 * 1024}
        )

        assert [p for p, _ in found] == [big]

    def test_standard_plan_sets_thumbnail_threshold(self):
        assert SmartCleaner.STANDARD_PLAN["thumbnail_cache"]["min_file_bytes"] == 1024 * 1024

    async def test_deferred_category_reports_waiting_size(self, tmp_path, monkeypatch):
        cleaner = SmartCleaner(
            [
                {
                    "category_id": "tg",
                    "paths": [str(tmp_path / "tg")],
                    "requires_closed": ["Telegram.exe"],
                }
            ]
        )
        make_file(tmp_path / "tg" / "cache.bin", 10)
        monkeypatch.setattr(cleaner, "_running_process_names", lambda: {"telegram.exe"})

        summary = await cleaner.perform_deep_cleanup(
            {"tg": {"clean": True}},
            {"tg": {"folders_to_clean": [str(tmp_path / "tg")], "total_size": 3 * 1024**3}},
        )

        assert summary["skipped_categories"]["tg"] == "запущено: Telegram.exe — ждёт 3.0 ГБ"
        assert summary["deferred"] == [
            {"processes": ["Telegram.exe"], "folder": "", "size_bytes": 3 * 1024**3}
        ]
