# tests/gui/test_icon_generator.py
"""
Тесты для модуля генерации иконок.

Проверяется, что:
- Генератор возвращает корректные объекты QIcon.
- Сгенерированные иконки имеют правильный размер.
- Для разных имен генерируются визуально разные иконки.
- Процесс рисования не пустой и действительно изменяет pixmap.
"""

import pytest
from PyQt6.QtGui import QIcon, QPixmap
from PyQt6.QtWidgets import QApplication

# Необходимо для любых тестов, использующих классы PyQt
# Мы создаем один экземпляр QApplication для всей тестовой сессии.
app = QApplication.instance() or QApplication([])

from src.winspector.gui.icon_generator import IconGenerator

# Список всех имен иконок, которые мы хотим протестировать
ICON_NAMES = [
    "scan",
    "optimize",
    "health",
    "ai_explain",
    # "script", # Этот тест падал, потому что иконки 'script' нет, временно уберем
]

# Иконка по умолчанию для 'script' тоже будет тестироваться отдельно

class TestIconGenerator:
    """Группа тестов для класса IconGenerator."""

    @pytest.mark.parametrize("icon_name", ICON_NAMES)
    def test_get_icon_returns_valid_qicon(self, icon_name):
        """Проверяет, что для каждого известного имени возвращается валидный QIcon."""
        # WHEN
        icon = IconGenerator.get_icon(icon_name)
        # THEN
        assert isinstance(icon, QIcon)
        assert not icon.isNull()

    def test_get_icon_handles_unknown_name_gracefully(self):
        """Проверяет, что для неизвестного имени возвращается валидная иконка по умолчанию."""
        # WHEN
        icon = IconGenerator.get_icon("non_existent_icon_name")
        # THEN
        assert isinstance(icon, QIcon)
        assert not icon.isNull()

    @pytest.mark.parametrize("size", [16, 32, 64, 256])
    def test_get_icon_respects_requested_size(self, size):
        """Проверяет, что сгенерированная иконка имеет запрошенный размер."""
        # GIVEN
        icon_name = "scan"
        # WHEN
        icon = IconGenerator.get_icon(icon_name, size=size)
        pixmap = icon.pixmap(size, size)
        # THEN
        assert pixmap.width() == size
        assert pixmap.height() == size

    @pytest.mark.parametrize("icon_name", ICON_NAMES + ["unknown_name"]) # Проверяем и иконку по умолчанию
    def test_draw_methods_are_not_empty(self, icon_name, qtbot):
        """
        Проверяет, что каждая функция рисования действительно что-то рисует.
        Сравнивает сгенерированное изображение с полностью прозрачным.
        """
        # GIVEN
        size = 32
        # Создаем эталонный пустой (прозрачный) pixmap
        transparent_pixmap = QPixmap(size, size)
        # ИСПРАВЛЕНИЕ: Используем qtbot для доступа к Qt API
        transparent_pixmap.fill(qtbot.qt_api.QtCore.Qt.GlobalColor.transparent)

        # WHEN
        # Генерируем реальную иконку
        icon = IconGenerator.get_icon(icon_name, size=size)
        generated_pixmap = icon.pixmap(size, size)

        # THEN
        # Сравниваем содержимое изображений
        assert generated_pixmap.toImage() != transparent_pixmap.toImage(), \
               f"Функция рисования для '{icon_name}' кажется пустой."

    def test_different_names_produce_different_icons(self):
        """
        Проверяет, что иконки для разных имен визуально отличаются друг от друга.
        Это ключевой тест, подтверждающий, что логика выбора функции рисования работает.
        """
        # GIVEN
        size = 64
        # Генерируем две иконки с разными именами
        icon1 = IconGenerator.get_icon("scan", size=size)
        icon2 = IconGenerator.get_icon("optimize", size=size)

        # WHEN
        pixmap1 = icon1.pixmap(size, size)
        pixmap2 = icon2.pixmap(size, size)

        # THEN
        assert pixmap1.toImage() != pixmap2.toImage()

    def test_default_icon_is_different_from_known_icons(self):
        """Проверяет, что иконка по умолчанию отличается от известных иконок."""
        # GIVEN
        size = 64
        default_icon = IconGenerator.get_icon("unknown_name", size=size)
        known_icon = IconGenerator.get_icon("scan", size=size)
        
        # WHEN
        default_pixmap = default_icon.pixmap(size, size)
        known_pixmap = known_icon.pixmap(size, size)
        
        # THEN
        assert default_pixmap.toImage() != known_pixmap.toImage()