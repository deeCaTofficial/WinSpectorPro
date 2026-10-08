# src/winspector/core/credentials.py
"""
Хранение ключа Gemini на машине пользователя.

Ключ шифруется механизмом Windows DPAPI: расшифровать файл может только та
же учётная запись на том же компьютере, поэтому копирование файла на другую
машину ничего не даёт. Пароль пользователя при этом нигде не запрашивается.

Файл лежит в `%LOCALAPPDATA%`, а не рядом с программой: приложение могут
распаковать в `Program Files`, куда обычному пользователю нет записи.

Порядок поиска ключа:

1.  Переменная окружения `GEMINI_API_KEY` — удобно для разработки и CI.
2.  Сохранённый через интерфейс ключ.

Открытый текст ключа нигде не логируется.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

try:
    import win32crypt
except ImportError:  # окружения без pywin32 (например, Linux в CI)
    win32crypt = None

ENV_VAR = "GEMINI_API_KEY"
_ENTROPY_DESCRIPTION = "WinSpector Pro API key"

# Ключи Gemini выглядят как `AIza...`, но точный формат провайдер может
# менять, поэтому проверяем лишь то, что строка похожа на ключ, а не мусор.
MIN_KEY_LENGTH = 20
MAX_KEY_LENGTH = 256


def storage_path() -> Path:
    """Путь к файлу с зашифрованным ключом."""
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    root = Path(base) if base else Path.home()
    return root / "WinSpectorPro" / "credentials.dat"


def is_supported() -> bool:
    """Доступно ли шифрованное хранилище на этой системе."""
    return win32crypt is not None


def looks_like_key(value: str | None) -> bool:
    """Грубая проверка формата, чтобы отсеять пустые строки и опечатки."""
    if not value:
        return False
    candidate = value.strip()
    if not MIN_KEY_LENGTH <= len(candidate) <= MAX_KEY_LENGTH:
        return False
    # Пробелы внутри означают, что скопировали лишнее. Кириллица и прочие
    # не-ASCII символы в ключе невозможны — это опечатка или чужой текст.
    return candidate.isascii() and not any(character.isspace() for character in candidate)


def save_api_key(api_key: str) -> None:
    """
    Сохраняет ключ в зашифрованном виде.

    Raises:
        RuntimeError: если DPAPI недоступен или файл не удалось записать.
        ValueError: если ключ не похож на настоящий.
    """
    candidate = (api_key or "").strip()
    if not looks_like_key(candidate):
        raise ValueError("Ключ выглядит некорректным: проверьте, что скопирован целиком.")
    if win32crypt is None:
        raise RuntimeError("Защищённое хранилище недоступно: не установлен pywin32.")

    try:
        blob = win32crypt.CryptProtectData(
            candidate.encode("utf-8"), _ENTROPY_DESCRIPTION, None, None, None, 0
        )
    except Exception as exc:
        raise RuntimeError(f"Не удалось зашифровать ключ: {exc}") from exc

    target = storage_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(blob)
    except OSError as exc:
        raise RuntimeError(f"Не удалось сохранить ключ в '{target}': {exc}") from exc

    logger.info("Ключ API сохранён в защищённом хранилище.")


def load_stored_key() -> str | None:
    """Читает сохранённый ключ. Возвращает None, если его нет или он повреждён."""
    if win32crypt is None:
        return None

    source = storage_path()
    if not source.is_file():
        return None

    try:
        blob = source.read_bytes()
        _, plaintext = win32crypt.CryptUnprotectData(blob, None, None, None, 0)
    except Exception as exc:
        logger.warning("Не удалось прочитать сохранённый ключ: %s", exc)
        return None

    key = plaintext.decode("utf-8", errors="ignore").strip()
    return key if looks_like_key(key) else None


def delete_api_key() -> bool:
    """Удаляет сохранённый ключ. Возвращает True, если файл существовал."""
    target = storage_path()
    try:
        target.unlink()
    except FileNotFoundError:
        return False
    except OSError as exc:
        logger.warning("Не удалось удалить ключ: %s", exc)
        return False

    logger.info("Сохранённый ключ API удалён.")
    return True


def resolve_api_key() -> str | None:
    """Возвращает действующий ключ: сначала окружение, затем хранилище."""
    from_env = (os.environ.get(ENV_VAR) or "").strip()
    if looks_like_key(from_env):
        return from_env
    return load_stored_key()


def has_api_key() -> bool:
    """Есть ли ключ, пригодный для запросов к ИИ."""
    return resolve_api_key() is not None
