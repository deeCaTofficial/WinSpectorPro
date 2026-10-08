# tests/test_installer_leftovers.py
"""
Тесты поиска остатков установщиков.

Детекторы — чистые функции: список установленного передаётся параметром,
а файлы живут в `tmp_path`. Настоящий `C:\\Windows\\Installer` не читается.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from winspector.core.modules import installer_leftovers as il
from winspector.core.modules import leftover_scanner as ls
from winspector.core.modules import quarantine

OLD = time.time() - 90 * 86400


def make_file(path: Path, size: int = 10, mtime: float = OLD) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    os.utime(path, (mtime, mtime))
    return path


def inventory(
    *packages: str, products: tuple[str, ...] = ("{AAAAAAAA-0000-0000-0000-000000000000}",)
):
    return il.MsiInventory(
        product_codes=set(products),
        local_packages={p.lower() for p in packages},
        complete=True,
    )


class TestOrphanedMsiPackages:
    def test_unreferenced_old_package_is_found(self, tmp_path):
        keep = make_file(tmp_path / "1a2b.msi")
        make_file(tmp_path / "3c4d.msi", 500)
        make_file(tmp_path / "5e6f.msp", 300)
        make_file(tmp_path / "7a8b.msi")

        found = il.find_orphaned_msi_packages(
            tmp_path, inventory(str(keep), str(tmp_path / "5e6f.msp"), str(tmp_path / "7a8b.msi"))
        )

        assert [Path(f.path).name for f in found] == ["3c4d.msi"]
        assert found[0].size_bytes == 500
        assert found[0].kind == "msi_orphan"

    def test_reference_by_file_name_is_enough(self, tmp_path):
        """Путь в реестре бывает записан иначе — совпадение имени сохраняет пакет."""
        make_file(tmp_path / "1a2b.msi")
        found = il.find_orphaned_msi_packages(
            tmp_path, inventory(r"C:\WINDOWS\INSTAL~1\1a2b.msi", str(tmp_path / "x.msi"))
        )
        assert found == []

    def test_recent_package_is_kept(self, tmp_path):
        make_file(tmp_path / "ref.msi")
        make_file(tmp_path / "new.msi", mtime=time.time() - 86400)
        make_file(tmp_path / "ref2.msi")
        found = il.find_orphaned_msi_packages(
            tmp_path, inventory(str(tmp_path / "ref.msi"), str(tmp_path / "ref2.msi"))
        )
        assert found == []

    def test_incomplete_inventory_finds_nothing(self, tmp_path):
        """Без полного списка продуктов (нет прав администратора) сирот не ищем."""
        make_file(tmp_path / "3c4d.msi")
        partial = il.MsiInventory(complete=False, error="код 5")
        assert il.find_orphaned_msi_packages(tmp_path, partial) == []

    def test_mostly_orphans_means_broken_inventory(self, tmp_path):
        for name in ("a.msi", "b.msi", "c.msi"):
            make_file(tmp_path / name)
        found = il.find_orphaned_msi_packages(tmp_path, inventory(str(tmp_path / "a.msi")))
        assert found == [], "две трети «сирот» — признак неполного списка, а не мусора"

    def test_non_installer_files_are_ignored(self, tmp_path):
        make_file(tmp_path / "ref.msi")
        make_file(tmp_path / "ref2.msi")
        make_file(tmp_path / "icon.ico")
        (tmp_path / "{GUID}").mkdir()
        found = il.find_orphaned_msi_packages(
            tmp_path, inventory(str(tmp_path / "ref.msi"), str(tmp_path / "ref2.msi"))
        )
        assert found == []


GUID_A = "{11111111-2222-3333-4444-555555555555}"
GUID_B = "{66666666-7777-8888-9999-000000000000}"


class TestOrphanedPackageCache:
    def test_uninstalled_product_cache_is_found(self, tmp_path):
        make_file(tmp_path / f"{GUID_A}v9.0.11" / "dotnet-runtime-9.0.11-win-x64.msi", 700)

        found = il.find_orphaned_package_cache([tmp_path], inventory(), il.RegisteredInstallers())

        assert len(found) == 1
        assert found[0].size_bytes == 700
        assert "dotnet-runtime-9.0.11" in found[0].reason

    @pytest.mark.parametrize("keep_by", ["product", "bundle", "dependency", "uninstall_text"])
    def test_anything_still_referencing_the_cache_keeps_it(self, tmp_path, keep_by):
        name = f"{GUID_A}v9.0.11"
        make_file(tmp_path / name / "pkg.msi")
        inv = inventory(products=(GUID_A,)) if keep_by == "product" else inventory()
        registered = il.RegisteredInstallers(
            bundle_ids={GUID_A} if keep_by == "bundle" else set(),
            dependency_ids={GUID_A} if keep_by == "dependency" else set(),
            referenced_text=(
                rf"c:\programdata\package cache\{name.lower()}\setup.exe"
                if keep_by == "uninstall_text"
                else ""
            ),
        )
        assert il.find_orphaned_package_cache([tmp_path], inv, registered) == []

    def test_recent_or_unrecognised_dirs_are_kept(self, tmp_path):
        make_file(tmp_path / f"{GUID_B}v1.0" / "pkg.msi", mtime=time.time() - 3600)
        make_file(tmp_path / "SomeVendorCache" / "pkg.msi")
        assert (
            il.find_orphaned_package_cache([tmp_path], inventory(), il.RegisteredInstallers()) == []
        )

    def test_incomplete_inventory_finds_nothing(self, tmp_path):
        make_file(tmp_path / f"{GUID_A}v9.0.11" / "pkg.msi")
        partial = il.MsiInventory(complete=False)
        assert il.find_orphaned_package_cache([tmp_path], partial, il.RegisteredInstallers()) == []


class TestSquirrelVersions:
    def make_app(self, root: Path, name: str, versions: dict[str, float]) -> Path:
        app = root / name
        make_file(app / "Update.exe", 5)
        for version, mtime in versions.items():
            make_file(app / f"app-{version}" / f"{name}.exe", 100, mtime)
            os.utime(app / f"app-{version}", (mtime, mtime))
        return app

    def test_previous_version_is_found(self, tmp_path):
        app = self.make_app(tmp_path, "Discord", {"1.0.9257": OLD - 86400, "1.0.9258": OLD})

        found = il.find_superseded_squirrel_versions(tmp_path, set())

        assert [Path(f.path).name for f in found] == ["app-1.0.9257"]
        assert "app-1.0.9258" in found[0].reason
        assert (app / "app-1.0.9258").exists()

    def test_versions_are_compared_numerically(self, tmp_path):
        """app-1.0.10 новее app-1.0.9 — строковое сравнение ошиблось бы."""
        self.make_app(tmp_path, "Slack", {"1.0.9": OLD, "1.0.10": OLD})
        found = il.find_superseded_squirrel_versions(tmp_path, set())
        assert [Path(f.path).name for f in found] == ["app-1.0.9"]

    def test_fresh_update_keeps_previous_version(self, tmp_path):
        self.make_app(tmp_path, "Figma", {"126.8.16": OLD, "126.8.18": time.time() - 86400})
        assert il.find_superseded_squirrel_versions(tmp_path, set()) == []

    def test_running_old_version_is_kept(self, tmp_path):
        app = self.make_app(tmp_path, "Discord", {"1.0.1": OLD, "1.0.2": OLD})
        live = {str(app / "app-1.0.1").lower()}
        assert il.find_superseded_squirrel_versions(tmp_path, live) == []

    def test_without_update_exe_nothing_is_touched(self, tmp_path):
        app = tmp_path / "NotSquirrel"
        make_file(app / "app-1.0" / "x.exe")
        make_file(app / "app-2.0" / "x.exe")
        assert il.find_superseded_squirrel_versions(tmp_path, set()) == []


class TestIntegration:
    def test_scan_leftovers_adds_installer_findings(self, tmp_path, monkeypatch):
        finding = il.InstallerFinding(
            path=str(tmp_path / "x.msi"),
            kind="msi_orphan",
            reason="сирота",
            size_bytes=9,
            file_count=1,
        )
        monkeypatch.setattr(il, "collect", lambda live, now=None: ([finding], ["заметка"]))

        report = ls.scan_leftovers(ls.InstalledIndex(), [], roots={}, include_installers=True)

        (candidate,) = report.candidates
        assert candidate.kind == "msi_orphan"
        assert candidate.confidence == "high"
        assert candidate.prune_parent is False
        assert report.notes == ["заметка"]

    def test_explicit_roots_do_not_touch_the_real_system(self, monkeypatch):
        def explode(*args, **kwargs):
            raise AssertionError("collect() не должен вызываться с явными корнями")

        monkeypatch.setattr(il, "collect", explode)
        ls.scan_leftovers(ls.InstalledIndex(), [], roots={})

    def test_quarantined_file_keeps_its_parent_directory(self, tmp_path):
        installer = tmp_path / "Installer"
        package = make_file(installer / "3c4d.msi", 50)

        result = quarantine.quarantine_paths([(package, 50, "сирота", False)], root=tmp_path / "Q")

        assert not package.exists()
        assert installer.is_dir(), "каталог установщика не удаляется, даже опустев"
        assert Path(result.items[0].stored).is_file()
        restored, errors = quarantine.restore_batch(Path(result.batch_dir))
        assert (restored, errors) == (1, {})
        assert package.is_file()


@pytest.mark.windows
class TestAgainstRealSystem:
    def test_inventory_read_is_safe_without_admin(self):
        inventory_ = il.read_msi_inventory()
        # Без администратора — честная ошибка, с ним — полный список.
        assert inventory_.complete or inventory_.error

    def test_collect_is_read_only(self):
        findings, notes = il.collect(set())
        for finding in findings:
            assert Path(finding.path).exists()
        assert isinstance(notes, list)
