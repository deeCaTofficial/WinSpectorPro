# tests/test_credentials.py
"""
Тесты хранилища ключа Gemini.

Проверяется главное: ключ не лежит на диске в открытом виде, повреждённый
файл не роняет приложение, а переменная окружения имеет приоритет.
"""

from __future__ import annotations

import os

import pytest

from winspector.core import credentials

# Заведомо фиктивное значение. Формат настоящего ключа Gemini здесь
# намеренно не воспроизводится: иначе secret scanning на GitHub
# отмечал бы этот файл как утечку при каждом коммите.
REAL_LOOKING_KEY = "test-only-fake-credential-0000000000"


@pytest.fixture(autouse=True)
def isolated_storage(tmp_path, monkeypatch):
    """Уводит хранилище во временный каталог, не трогая настоящий ключ."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv(credentials.ENV_VAR, raising=False)
    yield


class TestKeyValidation:
    @pytest.mark.parametrize(
        "value",
        [REAL_LOOKING_KEY, "a" * 20, "x" * 256],
    )
    def test_accepts_plausible_keys(self, value):
        assert credentials.looks_like_key(value)

    @pytest.mark.parametrize(
        "value",
        [
            None,
            "",
            "   ",
            "short",
            "x" * 257,
            "ключ с пробелами внутри строки",
            "AIzaSy Key With Spaces 12345678",
            "ВАШ_API_КЛЮЧ_ЗДЕСЬ",  # плейсхолдер из .env.example
        ],
    )
    def test_rejects_garbage(self, value):
        assert not credentials.looks_like_key(value)


@pytest.mark.skipif(os.name != "nt", reason="DPAPI доступен только в Windows")
class TestStorage:
    def test_save_and_load_roundtrip(self):
        credentials.save_api_key(REAL_LOOKING_KEY)
        assert credentials.load_stored_key() == REAL_LOOKING_KEY

    def test_key_is_not_stored_in_plain_text(self):
        """Ключевая гарантия: файл нельзя прочитать глазами."""
        credentials.save_api_key(REAL_LOOKING_KEY)

        raw = credentials.storage_path().read_bytes()

        assert REAL_LOOKING_KEY.encode("utf-8") not in raw
        assert REAL_LOOKING_KEY.encode("utf-16-le") not in raw

    def test_missing_file_returns_none(self):
        assert credentials.load_stored_key() is None

    def test_corrupted_file_does_not_raise(self):
        target = credentials.storage_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes("это не зашифрованные данные".encode())

        assert credentials.load_stored_key() is None

    def test_invalid_key_is_rejected_before_saving(self):
        with pytest.raises(ValueError):
            credentials.save_api_key("short")
        assert not credentials.storage_path().exists()

    def test_delete_removes_the_key(self):
        credentials.save_api_key(REAL_LOOKING_KEY)
        assert credentials.delete_api_key() is True
        assert credentials.load_stored_key() is None

    def test_delete_on_empty_storage_is_safe(self):
        assert credentials.delete_api_key() is False

    def test_storage_lives_outside_the_application_folder(self, tmp_path):
        """Программу могут распаковать в Program Files без прав на запись."""
        assert str(tmp_path) in str(credentials.storage_path())


class TestResolution:
    def test_environment_variable_wins(self, monkeypatch):
        monkeypatch.setenv(credentials.ENV_VAR, REAL_LOOKING_KEY)
        assert credentials.resolve_api_key() == REAL_LOOKING_KEY
        assert credentials.has_api_key()

    def test_placeholder_in_environment_is_ignored(self, monkeypatch):
        """`.env.example` содержит заглушку — принимать её нельзя."""
        monkeypatch.setenv(credentials.ENV_VAR, "ВАШ_API_КЛЮЧ_ЗДЕСЬ")
        assert credentials.resolve_api_key() is None

    def test_no_key_anywhere(self):
        assert credentials.resolve_api_key() is None
        assert not credentials.has_api_key()

    @pytest.mark.skipif(os.name != "nt", reason="DPAPI доступен только в Windows")
    def test_falls_back_to_storage(self):
        credentials.save_api_key(REAL_LOOKING_KEY)
        assert credentials.resolve_api_key() == REAL_LOOKING_KEY
