# tests/test_cleanup_engine.py
"""
Тесты движка очистки: три режима одного обхода.

Все операции идут во временном каталоге pytest. Режим `audit` обязан быть
строго read-only — это проверяется отдельно.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from winspector.core.modules import cleanup_engine as engine


def make_file(path: Path, size: int = 10, age_days: float = 0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    if age_days:
        stamp = time.time() - age_days * 86400
        os.utime(path, (stamp, stamp))
    return path


def snapshot(root: Path) -> set[str]:
    return {str(p.relative_to(root)) for p in root.rglob("*")}


class TestScan:
    def test_counts_only_what_would_be_deleted(self, tmp_path):
        make_file(tmp_path / "old.bin", 700, age_days=3)
        make_file(tmp_path / "sub" / "fresh.bin", 300)

        result = engine.process_tree(tmp_path, mode="scan", min_age_seconds=86400)

        assert result.freed_bytes == 700
        assert result.counts["too_recent"] == 1
        assert result.sizes["too_recent"] == 300
        assert result.dirs_seen == 1

    def test_missing_root_is_empty_result(self, tmp_path):
        result = engine.process_tree(tmp_path / "absent", mode="scan")
        assert result.freed_bytes == 0 and result.errors == 0

    def test_scan_changes_nothing(self, tmp_path):
        make_file(tmp_path / "a" / "b" / "c.bin")
        before = snapshot(tmp_path)
        engine.process_tree(tmp_path, mode="scan")
        assert snapshot(tmp_path) == before


class TestAudit:
    def test_audit_is_read_only(self, tmp_path):
        make_file(tmp_path / "a.bin", 10, age_days=2)
        make_file(tmp_path / "sub" / "b.bin", 10)
        (tmp_path / "empty").mkdir()
        before = snapshot(tmp_path)

        result = engine.process_tree(tmp_path, mode="audit")

        assert snapshot(tmp_path) == before
        assert result.freed_files == 2
        assert result.dirs_removed == 0

    def test_audit_detects_locked_file_without_deleting(self, tmp_path):
        busy = make_file(tmp_path / "busy.bin", 40)
        make_file(tmp_path / "free.bin", 10)

        with busy.open("rb"):
            result = engine.process_tree(tmp_path, mode="audit")

        assert result.counts["locked"] == 1
        assert result.sizes["locked"] == 40
        assert result.counts["deletable"] == 1
        assert busy.exists()
        assert "код 32" in result.samples["locked"][0].detail

    def test_lock_holder_is_identified(self, tmp_path):
        busy = make_file(tmp_path / "busy.bin", 1)
        with busy.open("rb"):
            holders = engine.find_lock_holders([str(busy)])
        assert holders, "держатель не найден"
        assert any(f"PID {os.getpid()}" in holder for holder in holders)

    def test_free_file_has_no_holders(self, tmp_path):
        free = make_file(tmp_path / "free.bin", 1)
        assert engine.find_lock_holders([str(free)]) == []

    def test_describe_access_reports_owner(self, tmp_path):
        target = make_file(tmp_path / "mine.bin", 1)
        info = engine.describe_access(str(target))
        assert info["owner"] and info["owner"] != "?"
        assert info["owner_sid"].startswith("S-1-")
        assert info["admins_can_delete"] is not None
        assert info["custom_acl"] in (True, False)

    def test_describe_access_survives_unsupported_ace(self, tmp_path, monkeypatch):
        """
        Регрессия: pywin32 не разбирает callback-ACE (тип 9) и бросал
        NotImplementedError — аудит падал на каталогах с такими правами.
        """
        win32security = pytest.importorskip("win32security")
        flags = win32security.OWNER_SECURITY_INFORMATION | win32security.DACL_SECURITY_INFORMATION
        real = win32security.GetFileSecurity(str(tmp_path), flags)

        class CallbackOnlyDacl:
            def GetAceCount(self):
                return 1

            def GetAce(self, _index):
                raise NotImplementedError("Ace type 9 is not supported yet")

        class Descriptor:
            def GetSecurityDescriptorOwner(self):
                return real.GetSecurityDescriptorOwner()

            def GetSecurityDescriptorDacl(self):
                return CallbackOnlyDacl()

            def GetSecurityDescriptorControl(self):
                return 0, 1

        monkeypatch.setattr(win32security, "GetFileSecurity", lambda *_args: Descriptor())

        info = engine.describe_access(str(tmp_path))

        assert info["custom_acl"] is True, "неразобранная запись считается ручной"
        assert info["admins_can_delete"] is False


class TestApply:
    def test_files_first_then_empty_dirs_bottom_up(self, tmp_path):
        make_file(tmp_path / "a" / "b" / "deep.bin")
        make_file(tmp_path / "a" / "side.bin")
        (tmp_path / "hollow" / "inner").mkdir(parents=True)

        result = engine.process_tree(tmp_path, mode="apply")

        assert result.counts["deleted"] == 2
        # a/b, a, hollow/inner, hollow — четыре каталога; корень сохраняется.
        assert result.dirs_removed == 4
        assert tmp_path.is_dir()
        assert snapshot(tmp_path) == set()

    def test_directory_with_kept_file_survives(self, tmp_path):
        fresh = make_file(tmp_path / "keep" / "fresh.bin")
        make_file(tmp_path / "keep" / "old.bin", age_days=5)

        result = engine.process_tree(tmp_path, mode="apply", min_age_seconds=3600)

        assert fresh.exists()
        assert (tmp_path / "keep").is_dir()
        assert result.dirs_removed == 0
        assert result.counts["deleted"] == 1
        assert result.dirs_errors == 0, "непустой каталог — не ошибка"

    def test_root_itself_is_never_removed(self, tmp_path):
        root = tmp_path / "root"
        root.mkdir()
        engine.process_tree(root, mode="apply")
        assert root.is_dir()

    def test_vanished_file_is_not_counted_as_freed(self, tmp_path, monkeypatch):
        target = make_file(tmp_path / "ghost.bin", 99)
        real_unlink = os.unlink

        def unlink_gone(path):
            real_unlink(path)
            raise FileNotFoundError(path)

        monkeypatch.setattr(engine.os, "unlink", unlink_gone)
        result = engine.process_tree(tmp_path, mode="apply")

        assert not target.exists()
        assert result.sizes["deleted"] == 0
        assert result.counts["error"] == 1


class TestAtomicSubdirs:
    """Подкаталог временной папки удаляется целиком или не трогается вовсе."""

    def test_subdir_with_fresh_file_is_kept_whole(self, tmp_path):
        old = make_file(tmp_path / "app_session" / "old.dat", 100, age_days=5)
        fresh = make_file(tmp_path / "app_session" / "fresh.dat", 10)

        result = engine.process_tree(
            tmp_path, mode="apply", min_age_seconds=86400, atomic_subdirs=True
        )

        assert old.exists(), "старый файл рабочей папки не удаляется из-под программы"
        assert fresh.exists()
        assert result.counts["in_use"] == 2
        assert result.sizes["in_use"] == 110
        assert result.counts["deleted"] == 0

    def test_subdir_with_locked_file_is_kept_whole(self, tmp_path):
        old = make_file(tmp_path / "_MEI1234" / "base.qss", 50, age_days=5)
        busy = make_file(tmp_path / "_MEI1234" / "python314.dll", 70, age_days=5)

        with busy.open("rb"):
            result = engine.process_tree(
                tmp_path, mode="apply", min_age_seconds=86400, atomic_subdirs=True
            )

        assert old.exists() and busy.exists()
        assert result.counts["in_use"] == 2

    def test_idle_subdir_is_removed_entirely(self, tmp_path):
        make_file(tmp_path / "old_setup" / "deep" / "a.bin", 30, age_days=10)
        make_file(tmp_path / "old_setup" / "b.bin", 20, age_days=10)

        result = engine.process_tree(
            tmp_path, mode="apply", min_age_seconds=86400, atomic_subdirs=True
        )

        assert not (tmp_path / "old_setup").exists(), "каталог удалён вместе с содержимым"
        assert result.sizes["deleted"] == 50
        assert result.dirs_removed == 2

    def test_root_files_are_still_judged_one_by_one(self, tmp_path):
        old = make_file(tmp_path / "old.tmp", 5, age_days=3)
        fresh = make_file(tmp_path / "fresh.tmp", 5)

        engine.process_tree(tmp_path, mode="apply", min_age_seconds=86400, atomic_subdirs=True)

        assert not old.exists()
        assert fresh.exists()

    def test_scan_reports_only_what_apply_would_delete(self, tmp_path):
        make_file(tmp_path / "busy" / "old.dat", 100, age_days=5)
        make_file(tmp_path / "busy" / "fresh.dat", 10)
        make_file(tmp_path / "idle" / "old.dat", 40, age_days=5)

        scan = engine.process_tree(
            tmp_path, mode="scan", min_age_seconds=86400, atomic_subdirs=True
        )

        assert scan.freed_bytes == 40
        assert scan.sizes["in_use"] == 110


class TestFirstLockedFile:
    def test_returns_locked_file(self, tmp_path):
        make_file(tmp_path / "a" / "free.bin")
        busy = make_file(tmp_path / "a" / "b" / "busy.bin")
        with busy.open("rb"):
            assert engine.first_locked_file(tmp_path) == str(busy)

    def test_empty_when_nothing_locked(self, tmp_path):
        make_file(tmp_path / "free.bin")
        assert engine.first_locked_file(tmp_path) == ""

    def test_missing_directory(self, tmp_path):
        assert engine.first_locked_file(tmp_path / "absent") == ""


class TestSamples:
    def test_samples_are_capped(self, tmp_path):
        for index in range(10):
            make_file(tmp_path / f"f{index}.bin")
        result = engine.process_tree(tmp_path, mode="scan", sample_limit=3)
        assert len(result.samples["deletable"]) == 3
        assert result.counts["deletable"] == 10

    def test_merge_accumulates(self, tmp_path):
        make_file(tmp_path / "one" / "a.bin", 5)
        make_file(tmp_path / "two" / "b.bin", 7)
        total = engine.TreeResult(root="*", mode="scan")
        total.merge(engine.process_tree(tmp_path / "one", mode="scan"))
        total.merge(engine.process_tree(tmp_path / "two", mode="scan"))
        assert total.freed_bytes == 12
        assert total.freed_files == 2


@pytest.mark.skipif(os.name != "nt", reason="junction-точки есть только в Windows")
class TestLinks:
    def test_junction_root_is_refused(self, tmp_path):
        import _winapi

        outside = make_file(tmp_path / "outside" / "victim.bin", 5)
        link = tmp_path / "link"
        _winapi.CreateJunction(str(outside.parent), str(link))

        result = engine.process_tree(link, mode="apply")

        assert outside.exists()
        assert result.counts["deleted"] == 0
        assert "junction" in result.walk_error

    def test_junction_is_never_entered_in_any_mode(self, tmp_path):
        import _winapi

        outside = make_file(tmp_path / "outside" / "victim.bin", 500)
        root = tmp_path / "root"
        root.mkdir()
        _winapi.CreateJunction(str(outside.parent), str(root / "junc"))

        scan = engine.process_tree(root, mode="scan")
        assert scan.freed_bytes == 0, "содержимое за junction не считается"

        audit = engine.process_tree(root, mode="audit")
        assert audit.samples["deletable"][0].is_link

        engine.process_tree(root, mode="apply")
        assert outside.exists()
        assert not (root / "junc").exists()
