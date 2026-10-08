# tests/test_leftovers.py
"""
Тесты поиска остатков удалённых программ и карантина.

Сканер получает индекс установленного и улики напрямую — реестр и ярлыки
настоящей машины не читаются; все каталоги живут в `tmp_path`.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from winspector.core.modules import leftover_scanner as ls
from winspector.core.modules import quarantine

OLD = time.time() - 90 * 86400


def make_file(path: Path, size: int = 10, mtime: float = OLD) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    os.utime(path, (mtime, mtime))
    return path


def evidence(*aliases: str, scope: str = "user", kind: str = "execution_trace") -> ls.Evidence:
    return ls.Evidence(kind, scope, f"улика для {aliases[0]}", set(aliases))


@pytest.fixture
def roots(tmp_path) -> dict[str, str]:
    layout = {
        "appdata": tmp_path / "Roaming",
        "localappdata": tmp_path / "Local",
        "programdata": tmp_path / "ProgramData",
        "program_files": tmp_path / "Program Files",
    }
    for path in layout.values():
        path.mkdir()
    return {kind: str(path) for kind, path in layout.items()}


def scan(roots, evidence_list, index=None, **kwargs):
    return ls.scan_leftovers(index or ls.InstalledIndex(), evidence_list, roots=roots, **kwargs)


# --- Нормализация ------------------------------------------------------------


class TestNormalize:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("Foo Bar 2.1", "foo bar"),
            ("Foo Bar (x64)", "foo bar"),
            ("FooBar Software Inc.", "foobar"),
            ("The Foo Launcher", "foo"),
            ("io.github.hyprismteam.hyprism-updater", "io github hyprismteam hyprism updater"),
        ],
    )
    def test_strips_versions_noise_and_punctuation(self, raw, expected):
        assert ls.normalize(raw) == expected

    def test_path_aliases_take_first_components_after_known_root(self, monkeypatch):
        monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\u\AppData\Local")
        aliases = ls.path_aliases(
            r"C:\Users\u\AppData\Local\Roblox\Versions\version-1\RobloxPlayerBeta.exe"
        )
        assert "roblox" in aliases
        assert "robloxplayerbeta" in aliases
        assert "versions" not in aliases, "общие части пути не становятся псевдонимами"

    def test_path_aliases_outside_known_roots_drop_the_drive(self):
        assert "gta rp" in ls.path_aliases(r"C:\Games\GTA5RP\RageMP\GTA5.exe", roots=[])


# --- Кандидаты -------------------------------------------------------------


class TestCandidates:
    def test_directory_with_evidence_and_no_signs_of_life_is_actionable(self, roots):
        target = Path(roots["appdata"]) / "FooBar"
        make_file(target / "settings.ini")

        report = scan(roots, [evidence("foobar")])

        (candidate,) = report.candidates
        assert candidate.confidence == "high"
        assert candidate.path == str(target)
        assert candidate.file_count == 1

    def test_directory_without_evidence_is_never_a_candidate(self, roots):
        """Сотни каталогов игр из Steam не совпадают с установленными по имени —
        без улики они не трогаются даже как «low»."""
        make_file(Path(roots["localappdata"]) / "AtomicHeart" / "save.sav")
        assert scan(roots, [evidence("something else")]).candidates == []

    def test_installed_program_is_kept_even_with_evidence(self, roots):
        make_file(Path(roots["appdata"]) / "FooBar" / "settings.ini")
        index = ls.InstalledIndex(aliases={"foobar"})

        assert scan(roots, [evidence("foobar")], index).candidates == []

    def test_executables_inside_mean_portable_program(self, roots):
        target = Path(roots["appdata"]) / "FooBar"
        make_file(target / "foobar.exe")

        (candidate,) = scan(roots, [evidence("foobar")]).candidates
        assert candidate.confidence == "low"
        assert "исполняемые" in candidate.kept_reason

    def test_recent_activity_keeps_directory(self, roots):
        target = Path(roots["appdata"]) / "FooBar"
        make_file(target / "settings.ini", mtime=time.time() - 3600)

        (candidate,) = scan(roots, [evidence("foobar")]).candidates
        assert candidate.confidence == "low"
        assert "менялись" in candidate.kept_reason

    def test_running_process_keeps_directory(self, roots):
        target = Path(roots["localappdata"]) / "FooBar"
        make_file(target / "data.db")
        index = ls.InstalledIndex(live_dirs={str(target / "bin").lower()})

        (candidate,) = scan(roots, [evidence("foobar")], index).candidates
        assert candidate.confidence == "low"
        assert "процесс" in candidate.kept_reason

    def test_protected_path_is_kept(self, roots):
        make_file(Path(roots["appdata"]) / "FooBar" / "x.ini")

        (candidate,) = scan(roots, [evidence("foobar")], is_safe=lambda _: False).candidates
        assert candidate.kept_reason == "путь под защитой"

    def test_deny_names_are_skipped(self, roots):
        make_file(Path(roots["localappdata"]) / "Microsoft" / "x.ini")
        make_file(Path(roots["localappdata"]) / "Packages" / "x.ini")

        assert scan(roots, [evidence("microsoft"), evidence("packages")]).candidates == []

    def test_vendor_directory_expands_to_children_only(self, roots):
        """`Vendor\\App` — кандидат сам `App`, а не весь каталог издателя."""
        vendor = Path(roots["appdata"]) / "Acme"
        make_file(vendor / "Gone" / "settings.ini")
        make_file(vendor / "Alive" / "settings.ini")

        report = scan(roots, [evidence("gone"), evidence("acme")])

        assert [Path(c.path).name for c in report.candidates] == ["Gone"]

    def test_programdata_requires_machine_scope_evidence(self, roots):
        target = Path(roots["programdata"]) / "FooBar"
        make_file(target / "log.txt")

        assert scan(roots, [evidence("foobar", scope="user")]).candidates == []
        (candidate,) = scan(roots, [evidence("foobar", scope="machine")]).candidates
        assert candidate.confidence == "high"

    def test_token_match_for_long_compound_names(self, roots):
        target = Path(roots["localappdata"]) / "io.github.hyprismteam.hyprism-updater"
        make_file(target / "log.txt")

        (candidate,) = scan(roots, [evidence("hyprism")]).candidates
        assert candidate.confidence == "high"

    def test_short_tokens_do_not_match(self, roots):
        make_file(Path(roots["localappdata"]) / "Foo Data Cache" / "log.txt")
        assert scan(roots, [evidence("foo")]).candidates == []

    def test_actionable_size_sums_high_only(self, roots):
        make_file(Path(roots["appdata"]) / "Gone" / "a.ini", 300)
        make_file(Path(roots["appdata"]) / "Portable" / "p.exe", 500)

        report = scan(roots, [evidence("gone"), evidence("portable")])

        assert report.actionable_bytes == 300
        assert len(report.candidates) == 2


class TestOrphanInstallDirs:
    def test_program_files_dir_without_binaries_is_a_leftover(self, roots):
        make_file(Path(roots["program_files"]) / "GoneApp" / "uninstall.log")

        (candidate,) = scan(roots, []).candidates
        assert candidate.confidence == "high"
        assert "исполняемых" in candidate.evidence[0]

    def test_program_files_dir_with_dll_is_alive(self, roots):
        make_file(Path(roots["program_files"]) / "LiveApp" / "core.dll")
        assert scan(roots, []).candidates == []

    def test_empty_program_files_dir_is_not_quarantined(self, roots):
        """Пустым каталогам карантин не нужен — их убирает проход по пустым папкам."""
        (Path(roots["program_files"]) / "Empty").mkdir()
        report = scan(roots, [])
        assert report.candidates == []
        assert [Path(d.path).name for d in report.empty_dirs] == ["Empty"]

    def test_empty_appdata_dir_with_evidence_is_not_quarantined(self, roots):
        (Path(roots["appdata"]) / "FooBar").mkdir()
        report = scan(roots, [evidence("foobar")])
        assert report.candidates == []
        assert [Path(d.path).name for d in report.empty_dirs] == ["FooBar"]

    def test_microsoft_directories_are_never_orphans(self, roots):
        make_file(Path(roots["program_files"]) / "Microsoft SDKs" / "Portable" / "list.xml")
        assert scan(roots, []).candidates == []

    def test_vendor_child_is_kept_when_vendor_has_binaries_elsewhere(self, roots):
        vendor = Path(roots["program_files"]) / "Acme"
        make_file(vendor / "Suite" / "suite.exe")
        make_file(vendor / "Docs" / "readme.txt")

        assert scan(roots, []).candidates == []

    def test_vendor_child_is_orphan_when_vendor_has_no_binaries(self, roots):
        vendor = Path(roots["program_files"]) / "Acme"
        make_file(vendor / "Gone" / "uninstall.log")

        (candidate,) = scan(roots, []).candidates
        assert Path(candidate.path) == vendor / "Gone"


class TestInstalledIndex:
    def test_live_process_directly_in_directory_counts(self):
        """Регрессия: процесс из самого каталога (а не из подкаталога) не замечался."""
        index = ls.InstalledIndex(live_dirs={r"c:\program files\foo"})
        assert index.has_live_process_under(Path(r"C:\Program Files\Foo"))
        assert index.has_live_process_under(Path(r"C:\Program Files"))
        assert not index.has_live_process_under(Path(r"C:\Program Files\Foobar"))

    def test_program_files_dir_counts_as_installed_only_with_binaries(self, monkeypatch, tmp_path):
        """Регрессия: любое имя из Program Files считалось установленной программой,
        и пустые каталоги удалённых программ не находились вовсе."""
        make_file(tmp_path / "LiveApp" / "live.exe")
        make_file(tmp_path / "GoneApp" / "uninstall.log")
        (tmp_path / "EmptyApp").mkdir()
        monkeypatch.setattr(ls, "_program_dirs", lambda: [str(tmp_path)])
        monkeypatch.setattr(ls, "_read_uninstall_entries", lambda: [])
        monkeypatch.setattr(ls, "_installed_store_packages", lambda: [])
        monkeypatch.setattr(ls.psutil, "process_iter", lambda *_a, **_k: [])
        monkeypatch.setattr(ls.psutil, "win_service_iter", lambda: [], raising=False)

        index = ls.build_installed_index()

        assert "liveapp" in index.aliases
        assert "goneapp" not in index.aliases
        assert "emptyapp" not in index.aliases


# --- Пустые каталоги программ ---------------------------------------------

FUTURE = time.time() + 30 * 86400  # все каталоги теста «старше недели»
INHERITED = {"owner_sid": "S-1-5-32-544", "custom_acl": False}


def find_empty(roots, index=None, *, access=INHERITED, now=FUTURE, **kwargs):
    return ls.find_empty_app_dirs(
        roots, index or ls.InstalledIndex(), now=now, access=lambda _p: access, **kwargs
    )


class TestEmptyAppDirs:
    def test_empty_program_files_dir_is_found(self, roots):
        app = Path(roots["program_files"]) / "Hypixel Studios"
        (app / "Hytale" / "logs").mkdir(parents=True)

        (found,) = find_empty(roots)

        assert found.path == str(app)
        assert found.kept_reason == ""
        assert found.dir_count == 3

    def test_any_file_in_the_tree_disqualifies(self, roots):
        make_file(Path(roots["program_files"]) / "Foo" / "sub" / "deep" / "x.log")
        assert find_empty(roots) == []

    @pytest.mark.skipif(os.name != "nt", reason="junction-точки есть только в Windows")
    def test_junction_inside_disqualifies(self, roots, tmp_path):
        import _winapi

        outside = tmp_path / "outside"
        outside.mkdir()
        app = Path(roots["program_files"]) / "Linked"
        app.mkdir()
        _winapi.CreateJunction(str(outside), str(app / "junc"))

        assert find_empty(roots) == []

    def test_recently_touched_dir_is_kept(self, roots):
        (Path(roots["program_files"]) / "JustInstalling").mkdir()

        (found,) = find_empty(roots, now=time.time())

        assert "7 дней" in found.kept_reason

    def test_vendor_children_are_checked_in_program_files(self, roots):
        vendor = Path(roots["program_files"]) / "Microsoft Visual Studio"
        make_file(vendor / "Installer" / "setup.exe")
        (vendor / "2019").mkdir()

        (found,) = find_empty(roots)

        assert found.path == str(vendor / "2019"), "имя «2019» проверяется по издателю"

    def test_vendor_children_are_not_checked_in_appdata(self, roots):
        """В AppData пустой подкаталог живой программы может быть ей нужен."""
        vendor = Path(roots["appdata"]) / "Origin"
        make_file(vendor / "Live" / "settings.ini")
        (vendor / "Cloud Saves").mkdir()

        assert find_empty(roots) == []

    def test_installed_name_keeps_appdata_dir_but_not_program_files(self, roots):
        (Path(roots["localappdata"]) / "Claude-Data").mkdir()
        (Path(roots["program_files"]) / "Claude").mkdir()
        index = ls.InstalledIndex(aliases={"claude"})

        found = {Path(d.path).name: d.kept_reason for d in find_empty(roots, index)}

        assert "установленной" in found["Claude-Data"], "совпало слово имени"
        assert found["Claude"] == "", "пустой каталог в Program Files не нужен никому"

    @pytest.mark.parametrize(
        ("kind", "acl", "expected"),
        [
            ("program_files", {"owner_sid": ls._SID_TRUSTED_INSTALLER}, "TrustedInstaller"),
            ("program_files", {"owner_sid": "S-1-5-32-544", "custom_acl": True}, "вручную"),
            ("program_files", {"owner_sid": "", "custom_acl": None}, "не читаются"),
            ("programdata", {"owner_sid": ls._SID_SYSTEM, "custom_acl": False}, "SYSTEM"),
            ("program_files", {"owner_sid": ls._SID_SYSTEM, "custom_acl": False}, ""),
        ],
    )
    def test_owner_and_permissions_decide_in_machine_roots(self, roots, kind, acl, expected):
        (Path(roots[kind]) / "Placeholder").mkdir()

        (found,) = find_empty(roots, access=acl)

        if expected:
            assert expected in found.kept_reason
        else:
            assert found.kept_reason == ""

    def test_permissions_are_not_consulted_in_the_profile(self, roots):
        """В профиле явные права стоят почти у всех каталогов — они ничего не значат."""
        (Path(roots["appdata"]) / "GoneTool").mkdir()

        (found,) = find_empty(roots, access={"owner_sid": "", "custom_acl": True})

        assert found.kept_reason == ""

    def test_deny_names_and_generic_names_are_skipped(self, roots):
        (Path(roots["program_files"]) / "WindowsPowerShell" / "Scripts").mkdir(parents=True)
        (Path(roots["localappdata"]) / "Packages" / "Some.App_123").mkdir(parents=True)
        (Path(roots["localappdata"]) / "Common").mkdir()

        assert find_empty(roots) == []

    def test_protected_path_is_kept(self, roots):
        (Path(roots["program_files"]) / "Guarded").mkdir()

        (found,) = find_empty(roots, is_safe=lambda _p: False)

        assert found.kept_reason == "путь под защитой"

    def test_huge_empty_tree_is_not_a_leftover(self, tmp_path):
        root = tmp_path / "Wide"
        for i in range(5):
            (root / f"d{i}").mkdir(parents=True)
        assert ls.empty_tree(root, limit=5) is None
        assert ls.empty_tree(root, limit=6) is not None


# --- Улики ----------------------------------------------------------------


class TestEvidence:
    def test_evidence_pointing_at_installed_program_is_dropped(self, monkeypatch):
        monkeypatch.setattr(ls, "_read_uninstall_entries", lambda: [])
        monkeypatch.setattr(ls, "_broken_shortcuts", lambda: [])
        monkeypatch.setattr(ls, "_ghost_registry_paths", lambda: [])
        monkeypatch.setattr(
            ls,
            "_dead_execution_traces",
            lambda: [
                ("MuiCache", r"C:\Program Files\Roblox\Versions\v1\RobloxPlayerBeta.exe", "machine")
            ],
        )
        index = ls.InstalledIndex(aliases={"roblox"})

        kept, ghosts = ls.collect_evidence(index)

        assert kept == [] and ghosts == []

    def test_ghost_uninstall_requires_install_location(self, monkeypatch, tmp_path):
        """MSI-пакеты без InstallLocation (VC++ Redistributable) — не призраки."""
        entries = [
            {
                "DisplayName": "Microsoft Visual C++ 2015 Redistributable",
                "DisplayIcon": str(tmp_path / "missing" / "vcredist.exe"),
                "scope": "machine",
            },
            {
                "DisplayName": "GoneApp",
                "InstallLocation": str(tmp_path / "missing" / "GoneApp"),
                "scope": "user",
            },
        ]
        monkeypatch.setattr(ls, "_read_uninstall_entries", lambda: entries)
        monkeypatch.setattr(ls, "_broken_shortcuts", lambda: [])
        monkeypatch.setattr(ls, "_ghost_registry_paths", lambda: [])
        monkeypatch.setattr(ls, "_dead_execution_traces", lambda: [])

        kept, ghosts = ls.collect_evidence(ls.InstalledIndex())

        assert ghosts == ["GoneApp"]
        assert [e.kind for e in kept] == ["ghost_uninstall"]
        assert "goneapp" in kept[0].aliases

    def test_install_path_filter_rejects_downloads_and_temp(self, monkeypatch):
        # Синтетический профиль: tmp_path лежит в %TEMP% и сам попал бы под фильтр.
        profile = r"C:\Users\u"
        monkeypatch.setenv("USERPROFILE", profile)
        monkeypatch.setenv("LOCALAPPDATA", profile + r"\AppData\Local")
        assert not ls._looks_like_install_path(profile + r"\Downloads\setup.exe")
        assert not ls._looks_like_install_path(profile + r"\AppData\Local\Temp\x\app.exe")
        assert not ls._looks_like_install_path(profile + r"\AppData\Roaming\Foo\foo.exe")
        assert not ls._looks_like_install_path(r"C:\ProgramData\Package Cache\{x}\bundle.exe")
        assert not ls._looks_like_install_path(r"C:\Program Files\Foo\unins000.exe")
        assert ls._looks_like_install_path(profile + r"\AppData\Local\Programs\Foo\foo.exe")
        assert ls._looks_like_install_path(r"C:\Program Files\Foo\foo.exe")
        assert ls._looks_like_install_path(r"D:\Games\Foo\foo.exe")

    def test_scope_follows_path_location(self, monkeypatch):
        monkeypatch.setenv("USERPROFILE", r"C:\Users\u")
        assert ls._scope_of_path(r"C:\Users\u\AppData\Local\Programs\x.exe") == "user"
        assert ls._scope_of_path(r"C:\Program Files\Foo\foo.exe") == "machine"


# --- Карантин ---------------------------------------------------------------


class TestQuarantine:
    def test_moves_directory_and_writes_manifest(self, tmp_path):
        source = tmp_path / "Roaming" / "Acme" / "Gone"
        make_file(source / "settings.ini", 40)
        root = tmp_path / "Quarantine"

        result = quarantine.quarantine_paths([(source, 40, "улика")], root=root)

        assert not source.exists()
        assert not source.parent.exists(), "опустевший каталог издателя тоже убран"
        assert len(result.items) == 1
        stored = Path(result.items[0].stored)
        assert (stored / "settings.ini").read_bytes() == b"x" * 40
        manifest = json.loads((Path(result.batch_dir) / "manifest.json").read_text("utf-8"))
        assert manifest["items"][0]["original"] == str(source)
        assert manifest["retention_days"] == quarantine.RETENTION_DAYS

    def test_non_empty_parent_is_left_alone(self, tmp_path):
        source = tmp_path / "Acme" / "Gone"
        make_file(source / "a.ini")
        make_file(tmp_path / "Acme" / "Alive" / "b.ini")

        quarantine.quarantine_paths([(source, 10, "улика")], root=tmp_path / "Q")

        assert (tmp_path / "Acme" / "Alive" / "b.ini").exists()

    def test_missing_source_is_reported_not_raised(self, tmp_path):
        result = quarantine.quarantine_paths([(tmp_path / "nope", 0, "улика")], root=tmp_path / "Q")
        assert result.items == []
        assert result.batch_dir == "", "пустая партия не создаётся"
        assert "отсутствует" in result.skipped[str(tmp_path / "nope")]

    def test_restore_returns_directory(self, tmp_path):
        source = tmp_path / "Gone"
        make_file(source / "settings.ini")
        result = quarantine.quarantine_paths([(source, 10, "улика")], root=tmp_path / "Q")

        restored, errors = quarantine.restore_batch(Path(result.batch_dir))

        assert (restored, errors) == (1, {})
        assert (source / "settings.ini").exists()
        assert not Path(result.batch_dir).exists()

    def test_restore_does_not_overwrite_existing(self, tmp_path):
        source = tmp_path / "Gone"
        make_file(source / "settings.ini")
        result = quarantine.quarantine_paths([(source, 10, "улика")], root=tmp_path / "Q")
        make_file(source / "new.ini")  # на исходном месте появилось что-то новое

        restored, errors = quarantine.restore_batch(Path(result.batch_dir))

        assert restored == 0
        assert "уже что-то есть" in errors[str(source)]

    def test_purge_removes_only_expired_batches(self, tmp_path):
        root = tmp_path / "Q"
        old_batch = root / "20200101-120000"
        fresh_batch = root / time.strftime("%Y%m%d-%H%M%S")
        for batch in (old_batch, fresh_batch):
            make_file(batch / "manifest.json", 2)
            make_file(batch / "001__x" / "f.txt")

        purged = quarantine.purge_expired(root=root, retention_days=30)

        assert purged == 1
        assert not old_batch.exists()
        assert fresh_batch.exists()

    def test_purge_ignores_foreign_directories(self, tmp_path):
        root = tmp_path / "Q"
        make_file(root / "not-a-batch" / "manifest.json", 2)
        assert quarantine.purge_expired(root=root) == 0
        assert (root / "not-a-batch").exists()


@pytest.mark.windows
class TestAgainstRealSystem:
    """Сборщики улик на настоящей машине: только чтение, без утверждений о составе."""

    def test_installed_index_is_populated(self):
        index = ls.build_installed_index()
        assert len(index.aliases) > 10
        assert all(isinstance(alias, str) for alias in index.aliases)

    def test_evidence_collection_survives_real_registry_and_shortcuts(self):
        index = ls.build_installed_index()
        evidence_list, ghosts = ls.collect_evidence(index)
        assert isinstance(evidence_list, list) and isinstance(ghosts, list)
        # Улика, указывающая на установленную программу, отфильтрована.
        assert all(not (item.aliases & index.aliases) for item in evidence_list)

    def test_full_scan_is_read_only_and_fast(self):
        import time as _time

        started = _time.perf_counter()
        report = ls.scan_leftovers()
        assert _time.perf_counter() - started < 30
        for candidate in report.candidates:
            assert Path(candidate.path).is_dir(), "сканер ничего не переносит и не удаляет"
