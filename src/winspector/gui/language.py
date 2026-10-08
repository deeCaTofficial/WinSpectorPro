"""Выбор языка интерфейса и его сохранение между запусками."""

from __future__ import annotations

import ctypes
import os
from pathlib import Path

from PyQt6.QtCore import QCoreApplication, QLibraryInfo, QSettings, QTranslator


def settings_path() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    return (Path(base) if base else Path.home()) / "WinSpectorPro" / "settings.ini"


def _settings() -> QSettings:
    return QSettings(str(settings_path()), QSettings.Format.IniFormat)


def windows_language() -> str:
    """Язык интерфейса Windows; все языки кроме русского используют английский."""
    try:
        # LANGID из GetUserDefaultUILanguage описывает язык интерфейса Windows,
        # в отличие от регионального формата чисел и дат.
        return "ru" if ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3FF == 0x19 else "en"
    except (AttributeError, OSError):
        return "en"


def get_language() -> str:
    settings = _settings()
    saved = settings.value("interface/language")
    if saved in ("ru", "en"):
        return saved
    detected = windows_language()
    settings.setValue("interface/language", detected)
    return detected


def set_language(language: str) -> None:
    if language not in ("ru", "en"):
        raise ValueError("Unsupported interface language")
    settings = _settings()
    settings.setValue("interface/language", language)
    settings.sync()
    if settings.status() != QSettings.Status.NoError:
        raise OSError(f"Could not save language preference to {settings_path()}")


def localize(language: str, russian: str, english: str) -> str:
    return english if language == "en" else russian


_qt_translator: QTranslator | None = None


def apply_qt_translation(language: str) -> None:
    """
    Переводит то, что рисует сам Qt: кнопки «Да/Нет» в сообщениях и
    контекстное меню полей. Без этого они остаются английскими в русском окне.
    """
    global _qt_translator
    app = QCoreApplication.instance()
    if app is None:
        return
    if _qt_translator is not None:
        app.removeTranslator(_qt_translator)
        _qt_translator = None
    if language != "ru":
        return
    translator = QTranslator(app)
    folder = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
    if translator.load("qtbase_ru", folder):
        app.installTranslator(translator)
        _qt_translator = translator
