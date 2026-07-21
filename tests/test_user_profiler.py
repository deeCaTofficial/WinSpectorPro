# tests/test_user_profiler.py
"""
Тесты профилировщика.

Реестр и файловая система подменяются, поэтому результат не зависит от того,
что установлено на конкретной машине.
"""

from __future__ import annotations

import os

import pytest

from winspector.core.modules.user_profiler import (
    MAX_SHORTCUTS_PER_LOCATION,
    MAX_SOFTWARE_ENTRIES,
    UserProfiler,
)


@pytest.fixture
def profiler() -> UserProfiler:
    return UserProfiler()


class FakePool:
    """Пул, возвращающий заранее заданный результат вместо WMI."""

    def __init__(self, result=None, error: Exception | None = None) -> None:
        self.result = result if result is not None else {"cpu": {"name": "Test CPU"}}
        self.error = error

    async def run(self, func, *args):
        if self.error:
            raise self.error
        return self.result


class TestSystemProfile:
    async def test_collects_all_sections(self, monkeypatch):
        profiler = UserProfiler(worker_pool=FakePool())
        monkeypatch.setattr(
            profiler, "_get_installed_software_from_registry", lambda: {"software_list": []}
        )
        monkeypatch.setattr(profiler, "_get_environment_variables", lambda: {})
        monkeypatch.setattr(profiler, "_get_desktop_and_start_menu_shortcuts", lambda: {})
        monkeypatch.setattr(profiler, "_get_user_folder_stats", lambda: {})
        monkeypatch.setattr(profiler, "_get_default_browser", lambda: "chrome.exe")

        profile = await profiler.get_system_profile()

        assert set(profile) == {
            "hardware",
            "installed_software",
            "environment_variables",
            "shortcuts",
            "user_folder_stats",
            "default_browser",
        }
        assert profile["hardware"] == {"cpu": {"name": "Test CPU"}}

    async def test_one_failing_collector_does_not_abort_the_rest(self, monkeypatch):
        """Сбой WMI не должен лишать модель остальных признаков."""
        profiler = UserProfiler(worker_pool=FakePool(error=RuntimeError("WMI упал")))

        def boom():
            raise OSError("нет доступа к реестру")

        monkeypatch.setattr(profiler, "_get_installed_software_from_registry", boom)
        monkeypatch.setattr(profiler, "_get_environment_variables", lambda: {"PATH": "x"})
        monkeypatch.setattr(profiler, "_get_desktop_and_start_menu_shortcuts", lambda: {})
        monkeypatch.setattr(profiler, "_get_user_folder_stats", lambda: {})
        monkeypatch.setattr(profiler, "_get_default_browser", lambda: None)

        profile = await profiler.get_system_profile()

        assert "error" in profile["hardware"]
        assert "error" in profile["installed_software"]
        assert profile["environment_variables"] == {"PATH": "x"}

    async def test_result_is_json_serialisable(self, monkeypatch):
        """Профиль уходит в промпт через json.dumps — он обязан сериализоваться."""
        import json

        profiler = UserProfiler(worker_pool=FakePool())
        monkeypatch.setattr(
            profiler, "_get_installed_software_from_registry", lambda: {"software_list": ["A"]}
        )
        monkeypatch.setattr(profiler, "_get_environment_variables", lambda: {})
        monkeypatch.setattr(profiler, "_get_desktop_and_start_menu_shortcuts", lambda: {})
        monkeypatch.setattr(profiler, "_get_user_folder_stats", lambda: {})
        monkeypatch.setattr(profiler, "_get_default_browser", lambda: None)

        profile = await profiler.get_system_profile()

        assert json.dumps(profile, default=str)


class TestEnvironmentVariables:
    def test_returns_only_present_variables(self, profiler, monkeypatch):
        monkeypatch.setenv("JAVA_HOME", "C:\\Java")
        monkeypatch.delenv("GOPATH", raising=False)

        result = profiler._get_environment_variables()

        assert result["JAVA_HOME"] == "C:\\Java"
        assert "GOPATH" not in result

    def test_ignores_unrelated_variables(self, profiler, monkeypatch):
        monkeypatch.setenv("SOME_SECRET_TOKEN", "shhh")
        assert "SOME_SECRET_TOKEN" not in profiler._get_environment_variables()


class TestFolderStats:
    def test_detects_existing_and_missing_folders(self, profiler, monkeypatch, tmp_path):
        existing = tmp_path / "Documents"
        (existing / "sub").mkdir(parents=True)

        def fake_expandvars(value: str) -> str:
            return value.replace("%USERPROFILE%", str(tmp_path))

        monkeypatch.setattr(os.path, "expandvars", fake_expandvars)

        stats = profiler._get_user_folder_stats()

        assert stats["documents"] == {"exists": True, "has_content": True}
        assert stats["videos"]["exists"] is False

    def test_reports_empty_folder(self, profiler, monkeypatch, tmp_path):
        (tmp_path / "Pictures").mkdir()
        monkeypatch.setattr(
            os.path, "expandvars", lambda v: v.replace("%USERPROFILE%", str(tmp_path))
        )

        stats = profiler._get_user_folder_stats()

        assert stats["pictures"] == {"exists": True, "has_content": False}

    def test_detects_git_config(self, profiler, monkeypatch, tmp_path):
        (tmp_path / ".gitconfig").write_text("[user]", encoding="utf-8")
        monkeypatch.setattr(
            os.path, "expandvars", lambda v: v.replace("%USERPROFILE%", str(tmp_path))
        )
        assert profiler._get_user_folder_stats().get("git_config_exists") is True


class TestShortcuts:
    def test_collects_shortcut_names(self, profiler, monkeypatch, tmp_path):
        desktop = tmp_path / "Desktop"
        desktop.mkdir()
        (desktop / "Steam.lnk").write_bytes(b"")
        (desktop / "VS Code.lnk").write_bytes(b"")
        (desktop / "notes.txt").write_bytes(b"")

        monkeypatch.setattr(
            os.path,
            "expandvars",
            lambda v: (
                v.replace("%USERPROFILE%", str(tmp_path))
                .replace("%PUBLIC%", str(tmp_path / "none"))
                .replace("%APPDATA%", str(tmp_path / "none"))
                .replace("%PROGRAMDATA%", str(tmp_path / "none"))
            ),
        )

        shortcuts = profiler._get_desktop_and_start_menu_shortcuts()

        assert set(shortcuts["user_desktop"]) == {"Steam", "VS Code"}

    def test_shortcut_list_is_capped(self, profiler, monkeypatch, tmp_path):
        """Тысячи ярлыков не должны раздувать промпт."""
        desktop = tmp_path / "Desktop"
        desktop.mkdir()
        for index in range(MAX_SHORTCUTS_PER_LOCATION + 40):
            (desktop / f"app{index}.lnk").write_bytes(b"")

        monkeypatch.setattr(
            os.path, "expandvars", lambda v: v.replace("%USERPROFILE%", str(tmp_path))
        )

        shortcuts = profiler._get_desktop_and_start_menu_shortcuts()

        assert len(shortcuts["user_desktop"]) == MAX_SHORTCUTS_PER_LOCATION

    def test_missing_directories_are_skipped(self, profiler, monkeypatch, tmp_path):
        monkeypatch.setattr(os.path, "expandvars", lambda v: str(tmp_path / "absent"))
        result = profiler._get_desktop_and_start_menu_shortcuts()
        assert all(names == [] for names in result.values())


@pytest.mark.skipif(os.name != "nt", reason="winreg доступен только в Windows")
class TestRegistry:
    def test_reads_real_registry_without_crashing(self, profiler):
        """Проверка на настоящем реестре: важно, что нет исключений."""
        result = profiler._get_installed_software_from_registry()

        assert isinstance(result["software_list"], list)
        assert all(isinstance(name, str) for name in result["software_list"])
        assert len(result["software_list"]) <= MAX_SOFTWARE_ENTRIES

    def test_default_browser_returns_string_or_none(self, profiler):
        result = profiler._get_default_browser()
        assert result is None or isinstance(result, str)
