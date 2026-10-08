# tests/conftest.py
"""Общие фикстуры для тестов WinSpector Pro."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
for candidate in (PROJECT_ROOT, PROJECT_ROOT / "src"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from winspector.core.modules.ai_base import AIBase  # noqa: E402

KB_DIR = PROJECT_ROOT / "src" / "winspector" / "data" / "knowledge_base"


@pytest.fixture(autouse=True)
def isolate_language_settings(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
):
    """Не читать и не менять пользовательский выбор языка при проверках."""
    from winspector.gui.language import set_language

    # Отдельный каталог, а не `tmp_path`: тесты очистки считают размер
    # `tmp_path`, и файл настроек (26 байт) попадал бы в их подсчёт.
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path_factory.mktemp("localappdata")))
    set_language("ru")


@pytest.fixture(autouse=True)
def isolate_leftovers(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """
    Ни один тест не должен искать остатки на настоящей машине и тем более
    переносить их в карантин: ядро в тестах видит пустой отчёт, а карантин
    по умолчанию живёт во временном каталоге pytest.
    """
    from winspector.core.analyzer import WinSpectorCore
    from winspector.core.modules import leftover_scanner, quarantine

    async def no_leftovers(self):
        return leftover_scanner.LeftoverReport()

    monkeypatch.setattr(WinSpectorCore, "_scan_leftovers", no_leftovers)
    monkeypatch.setattr(quarantine, "quarantine_root", lambda: tmp_path / "quarantine")


@pytest.fixture(autouse=True)
def isolate_ai_client(monkeypatch: pytest.MonkeyPatch):
    """
    Изолирует тесты от реального API.

    Общий клиент `AIBase` — атрибут класса, поэтому без сброса он утекал бы
    между тестами. Ключ подставляется фиктивный, чтобы случайный «живой»
    вызов упал сразу, а не ушёл в сеть.
    """
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    AIBase.reset_client()
    yield
    AIBase.reset_client()


@pytest.fixture
def ai_config() -> dict[str, Any]:
    """Конфигурация ядра с коротким TTL кеша."""
    return {
        "app_config": {
            "ai_model": "gemini-3.8-flash",
            "ai_cache_ttl": 3600,
            "ai_request_timeout": 5,
        }
    }


@pytest.fixture
def optimization_rules() -> list[dict[str, Any]]:
    """Небольшой набор правил оптимизации, покрывающий все ветки валидатора."""
    return [
        {
            "id": "Svc_MapsBroker",
            "type": "service",
            "safety": "high",
            "relevant_profiles": ["power_user"],
            "description_ru": "Служба загрузки карт.",
        },
        {
            "id": "Svc_SteamHelper",
            "type": "service",
            "safety": "medium",
            "relevant_profiles": ["gamer"],
            "protected_for_profiles": ["Gamer"],
            "targets": ["steamhelper"],
            "description_ru": "Вспомогательная служба Steam.",
        },
        {
            "id": "Svc_LegacyCritical",
            "type": "service",
            "safety": "critical",
            "targets": ["legacycritical"],
            "description_ru": "Критическая служба из базы знаний.",
        },
        {
            "id": "Svc_Universal",
            "type": "service",
            "safety": "high",
            "description_ru": "Правило без привязки к профилю.",
        },
    ]


@pytest.fixture
def cleanup_rules() -> list[dict[str, Any]]:
    """Правила очистки с разными уровнями безопасности."""
    return [
        {
            "category_id": "system_temp_files",
            "safety": "high",
            "cleanup_type": "folder",
            "paths": ["%TEMP%"],
            "description_ru": "Временные файлы.",
        },
        {
            "category_id": "python_pip_cache",
            "safety": "low",
            "cleanup_type": "folder",
            "paths": ["%LOCALAPPDATA%/pip/cache"],
            "description_ru": "Кеш pip.",
        },
        {
            "category_id": "browser_cache",
            "safety": "medium",
            "cleanup_type": "folder",
            "paths": ["%LOCALAPPDATA%/browser/cache"],
            "description_ru": "Кеш браузера.",
        },
    ]


@pytest.fixture
def knowledge_base(
    optimization_rules: list[dict[str, Any]], cleanup_rules: list[dict[str, Any]]
) -> dict[str, Any]:
    """Собранная база знаний в том виде, в каком её получает ядро."""
    return {
        "optimization_rules": optimization_rules,
        "cleanup_rules": cleanup_rules,
    }


@pytest.fixture
def junk_report() -> dict[str, Any]:
    """Результат сканирования: единственный допустимый источник путей."""
    return {
        "system_temp_files": {
            "category_id": "system_temp_files",
            "total_size": 2048,
            "files_to_delete": [],
            "folders_to_clean": [],
        },
        "browser_cache": {
            "category_id": "browser_cache",
            "total_size": 1024,
            "files_to_delete": [],
            "folders_to_clean": [],
        },
    }


@pytest.fixture
def real_knowledge_base() -> dict[str, Any]:
    """Настоящая база знаний из репозитория (для проверок целостности данных)."""
    import yaml

    if not KB_DIR.is_dir():
        pytest.skip(f"База знаний не найдена: {KB_DIR}")

    combined: dict[str, Any] = {}
    for yaml_file in sorted(KB_DIR.glob("*.yaml")):
        with yaml_file.open(encoding="utf-8") as handle:
            combined[yaml_file.stem] = yaml.safe_load(handle)
    return combined
