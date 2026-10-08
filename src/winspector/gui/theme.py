"""
Оформление приложения: палитра, шрифт, таблица стилей и тёмный заголовок окон.

Цвета заданы здесь один раз. Таблица стилей `resources/styles/main.qss`
ссылается на них через `{имя}`, а нарисованные вручную виджеты берут их
напрямую, поэтому окно, диалоги и полоса прогресса не расходятся по цвету.
"""

from __future__ import annotations

import ctypes
import logging
import sys
from pathlib import Path

from PyQt6.QtCore import QEvent, QObject, QRectF, Qt
from PyQt6.QtGui import (
    QColor,
    QFocusEvent,
    QFont,
    QFontDatabase,
    QGuiApplication,
    QIcon,
    QImage,
    QImageReader,
    QPainter,
    QPalette,
    QPixmap,
)
from PyQt6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QComboBox,
    QProxyStyle,
    QStyle,
    QStyleFactory,
    QWidget,
)

logger = logging.getLogger(__name__)

BG = "#101216"
SURFACE = "#171A20"
SURFACE_HOVER = "#1E222A"
INPUT = "#0C0E12"
BORDER = "#262B34"
BORDER_STRONG = "#363D49"
TEXT = "#E6E9EF"
MUTED = "#9AA3B2"
DIM = "#656E7C"
ACCENT = "#2F6FEB"
ACCENT_HOVER = "#3D7CF0"
ACCENT_PRESSED = "#2559C8"
ACCENT_TEXT = "#79A8FF"
ACCENT_SOFT = "rgba(47, 111, 235, 0.16)"
SUCCESS = "#3CCB7F"
SUCCESS_SOFT = "rgba(60, 203, 127, 0.14)"
WARNING = "#E8B04A"
WARNING_SOFT = "rgba(232, 176, 74, 0.14)"
DANGER = "#F2555A"
DANGER_SOFT = "rgba(242, 85, 90, 0.12)"
# Заливка кнопки необратимого действия: на #F2555A белый текст читается плохо.
DANGER_FILL = "#D23C42"
DANGER_FILL_HOVER = "#DE4A50"
DANGER_FILL_PRESSED = "#B9333A"

TOKENS = {
    name.lower(): value
    for name, value in globals().items()
    if name.isupper() and isinstance(value, str) and name != "TOKENS"
}

_FONT_FAMILIES = ("Segoe UI Variable Text", "Segoe UI")
_HEADING_FAMILIES = ("Segoe UI Variable Display Semibold", "Segoe UI Semibold")

_SOURCE_RESOURCES = Path(__file__).resolve().parents[1] / "resources"


def resource_path(*parts: str, base: Path | None = None) -> Path:
    """
    Путь к файлу из `resources`.

    В собранном EXE ресурсы лежат в `_MEIPASS/winspector/resources`, а не
    рядом с модулями, поэтому основной источник — `app_paths["base"]`.
    Без него (тесты, служебные скрипты) берётся папка рядом с исходниками.
    """
    root = base / "winspector" / "resources" if base else _SOURCE_RESOURCES
    return root.joinpath(*parts)


def _first_available(candidates: tuple[str, ...]) -> str | None:
    families = set(QFontDatabase.families())
    return next((name for name in candidates if name in families), None)


def heading_family() -> str:
    """Шрифт заголовков; без Segoe UI Variable (Windows 10) — обычный Segoe UI."""
    return _first_available(_HEADING_FAMILIES) or "Segoe UI"


# Значки берутся из системного шрифта значков Windows: они того же рисунка,
# что в самой Windows, и остаются чёткими при любом масштабе экрана.
_ICON_FAMILIES = ("Segoe Fluent Icons", "Segoe MDL2 Assets")
GLYPH_SETTINGS = "\ue713"
GLYPH_COPY = "\ue8c8"
GLYPH_CHECK = "\ue73e"


def icon_font(size: int) -> QFont | None:
    """\u0428\u0440\u0438\u0444\u0442 \u0437\u043d\u0430\u0447\u043a\u043e\u0432 Windows \u0437\u0430\u0434\u0430\u043d\u043d\u043e\u0433\u043e \u0440\u0430\u0437\u043c\u0435\u0440\u0430 \u0432 \u043f\u0438\u043a\u0441\u0435\u043b\u044f\u0445; \u0431\u0435\u0437 \u043d\u0435\u0433\u043e \u2014 None."""
    family = _first_available(_ICON_FAMILIES)
    if family is None:
        return None
    font = QFont(family)
    font.setPixelSize(size)
    return font


def glyph_icon(
    glyph: str, size: int = 16, color: str = MUTED, hover: str = TEXT, gap: int = 0
) -> QIcon:
    """
    Значок из шрифта Windows; при наведении и нажатии — цвет `hover`.

    `gap` — пустое место справа от значка: Qt ставит текст кнопки вплотную к
    значку, и без зазора они слипаются. Размер значка кнопки тогда `size + gap`.
    """
    font = icon_font(size)
    icon = QIcon()
    if font is None:
        return icon
    ratio = 2.0  # запас на масштаб экрана до 200 %
    for mode, tint in (
        (QIcon.Mode.Normal, color),
        (QIcon.Mode.Active, hover),
        (QIcon.Mode.Selected, hover),
        (QIcon.Mode.Disabled, DIM),
    ):
        pixmap = QPixmap(int((size + gap) * ratio), int(size * ratio))
        pixmap.setDevicePixelRatio(ratio)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setFont(font)
        painter.setPen(QColor(tint))
        painter.drawText(QRectF(0, 0, size, size), Qt.AlignmentFlag.AlignCenter, glyph)
        painter.end()
        icon.addPixmap(pixmap, mode)
    return icon


def _largest_enough_frame(path: Path, pixels: int) -> QImage:
    """Кадр файла, ближайший к `pixels` сверху; если все меньше — самый крупный."""
    reader = QImageReader(str(path))
    frames = [reader.read()]
    while reader.jumpToNextImage():
        frames.append(reader.read())
    frames = [frame for frame in frames if not frame.isNull()]
    if not frames:
        return QImage()
    enough = [frame for frame in frames if frame.width() >= pixels]
    if enough:
        return min(enough, key=QImage.width)
    return max(frames, key=QImage.width)


def scaled_pixmap(path: Path, size: int, ratio: float) -> QPixmap:
    """
    Картинка нужного размера с учётом масштаба экрана.

    Из многоразмерного .ico берётся наименьший кадр не меньше нужного:
    QPixmap читает только первый кадр (16 px), и растянутый логотип
    выходит мутным. Кадр уменьшается со сглаживанием, иначе Qt режет края.
    """
    pixels = round(size * ratio)
    source = _largest_enough_frame(path, pixels)
    if source.isNull():
        return QPixmap()
    pixmap = QPixmap.fromImage(source).scaled(
        pixels,
        pixels,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    pixmap.setDevicePixelRatio(ratio)
    return pixmap


def stylesheet(base: Path | None = None) -> str:
    """Таблица стилей с подставленными цветами и шрифтом заголовков."""
    text = resource_path("styles", "main.qss", base=base).read_text(encoding="utf-8")
    values = {
        **TOKENS,
        "heading_family": heading_family(),
        # Относительные url() в таблице стилей считаются от рабочей папки,
        # поэтому путь к иконкам подставляется полный.
        "icons": resource_path("icons", base=base).as_posix(),
    }
    for name, value in values.items():
        text = text.replace("{" + name + "}", value)
    return text


def _palette() -> QPalette:
    """Палитра для того, что таблица стилей не покрывает: системные диалоги, ссылки."""
    palette = QPalette()
    roles = {
        QPalette.ColorRole.Window: BG,
        QPalette.ColorRole.WindowText: TEXT,
        QPalette.ColorRole.Base: SURFACE,
        QPalette.ColorRole.AlternateBase: SURFACE_HOVER,
        QPalette.ColorRole.Text: TEXT,
        QPalette.ColorRole.Button: SURFACE,
        QPalette.ColorRole.ButtonText: TEXT,
        QPalette.ColorRole.Highlight: ACCENT,
        QPalette.ColorRole.HighlightedText: "#FFFFFF",
        QPalette.ColorRole.Link: ACCENT_TEXT,
        QPalette.ColorRole.LinkVisited: ACCENT_TEXT,
        QPalette.ColorRole.PlaceholderText: DIM,
        QPalette.ColorRole.ToolTipBase: SURFACE_HOVER,
        QPalette.ColorRole.ToolTipText: TEXT,
    }
    for role, color in roles.items():
        palette.setColor(role, QColor(color))
    for role in (
        QPalette.ColorRole.WindowText,
        QPalette.ColorRole.Text,
        QPalette.ColorRole.ButtonText,
    ):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor(DIM))
    return palette


_MESSAGE_GLYPHS = {
    QStyle.StandardPixmap.SP_MessageBoxInformation: ("\ue946", ACCENT_TEXT),
    QStyle.StandardPixmap.SP_MessageBoxQuestion: ("\ue9ce", ACCENT_TEXT),
    QStyle.StandardPixmap.SP_MessageBoxWarning: ("\ue7ba", WARNING),
    QStyle.StandardPixmap.SP_MessageBoxCritical: ("\uea39", DANGER),
}


class _AppStyle(QProxyStyle):
    """
    Fusion с двумя поправками.

    Значки сообщений — системные контурные вместо крупных глянцевых из Fusion.
    Подчёркнутые буквы быстрого доступа в кнопках скрыты, как и в самой Windows:
    Alt+буква при этом продолжает работать.
    """

    def standardIcon(self, standardIcon, option=None, widget=None):
        if glyph := _MESSAGE_GLYPHS.get(standardIcon):
            icon = glyph_icon(glyph[0], 28, glyph[1], glyph[1])
            if not icon.isNull():
                return icon
        return super().standardIcon(standardIcon, option, widget)

    def styleHint(self, hint, option=None, widget=None, returnData=None):
        if hint == QStyle.StyleHint.SH_UnderlineShortcut:
            return 0
        return super().styleHint(hint, option, widget, returnData)


def apply(app: QApplication, base: Path | None = None) -> None:
    """Применяет оформление ко всему приложению; `base` — как в `resource_path`."""
    # Стиль Windows 11 рисует часть элементов по-своему и спорит с таблицей
    # стилей; Fusion полностью ей подчиняется.
    if fusion := QStyleFactory.create("Fusion"):
        app.setStyle(_AppStyle(fusion))
    # Значки в контекстном меню полей — разномастные картинки Fusion.
    app.setAttribute(Qt.ApplicationAttribute.AA_DontShowIconsInMenus, True)
    if hints := app.styleHints():
        hints.setColorScheme(Qt.ColorScheme.Dark)
    app.setPalette(_palette())
    font = QFont(_first_available(_FONT_FAMILIES) or "Segoe UI")
    font.setPixelSize(14)
    app.setFont(font)
    app.setStyleSheet(stylesheet(base))
    app.installEventFilter(_FocusRing(app))
    if QGuiApplication.platformName() == "windows":
        app.installEventFilter(_TitleBarPainter(app))


# --- Рамка фокуса -------------------------------------------------------------------

# Причины фокуса, при которых человек сам ведёт его с клавиатуры.
_KEYBOARD_FOCUS = (
    Qt.FocusReason.TabFocusReason,
    Qt.FocusReason.BacktabFocusReason,
    Qt.FocusReason.ShortcutFocusReason,
)


class _FocusRing(QObject):
    """
    Показывает рамку фокуса у кнопок и списков только при работе с клавиатуры.

    В Qt рамка `:focus` видна при любом фокусе. Кнопка, на которую нажали
    мышью, сохраняет фокус, а после закрытия окна или Alt+Tab получает его
    обратно, и рамка висит без всякой причины. Здесь у виджета появляется
    свойство `focusRing`, и таблица стилей рисует рамку только по нему.
    Полям ввода это не нужно: им рамка показывает, куда пойдёт текст.
    """

    def eventFilter(self, watched: QObject | None, event: QEvent | None) -> bool:
        if event is not None and isinstance(watched, (QAbstractButton, QComboBox)):
            if event.type() == QEvent.Type.FocusIn and isinstance(event, QFocusEvent):
                _set_focus_ring(watched, event.reason() in _KEYBOARD_FOCUS)
            elif event.type() == QEvent.Type.FocusOut:
                _set_focus_ring(watched, False)
        return False


def _set_focus_ring(widget: QWidget, visible: bool) -> None:
    if bool(widget.property("focusRing")) == visible:
        return
    widget.setProperty("focusRing", visible)
    if style := widget.style():
        style.unpolish(widget)
        style.polish(widget)
    widget.update()


# --- Заголовок окна ---------------------------------------------------------------

_DWMWA_USE_IMMERSIVE_DARK_MODE = 20
_DWMWA_BORDER_COLOR = 34
_DWMWA_CAPTION_COLOR = 35
_DWMWA_TEXT_COLOR = 36


def _colorref(color: str) -> int:
    value = QColor(color)
    return value.red() | value.green() << 8 | value.blue() << 16


def style_title_bar(window: QWidget) -> None:
    """
    Красит системный заголовок окна в цвет приложения.

    Рамка остаётся системной: перетаскивание, привязка к краям экрана и
    изменение размера работают как в любой программе Windows. Цвет
    заголовка Windows 10 не поддерживает — там он просто становится тёмным.
    """
    if sys.platform != "win32":
        return
    try:
        dwm = ctypes.windll.dwmapi
        hwnd = ctypes.c_void_p(int(window.winId()))
        for attribute, value in (
            (_DWMWA_USE_IMMERSIVE_DARK_MODE, 1),
            (_DWMWA_CAPTION_COLOR, _colorref(BG)),
            (_DWMWA_TEXT_COLOR, _colorref(MUTED)),
            (_DWMWA_BORDER_COLOR, _colorref(BORDER)),
        ):
            data = ctypes.c_int(value)
            dwm.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(data), ctypes.sizeof(data))
    except (AttributeError, OSError):
        logger.debug("Не удалось изменить цвет заголовка окна.", exc_info=True)


class _TitleBarPainter(QObject):
    """Красит заголовок каждого окна приложения, включая сообщения и диалоги."""

    def eventFilter(self, watched: QObject | None, event: QEvent | None) -> bool:
        if (
            event is not None
            and event.type() == QEvent.Type.Show
            and isinstance(watched, QWidget)
            # Подсказки и выпадающие списки — тоже окна, но без заголовка.
            and watched.windowType() in (Qt.WindowType.Window, Qt.WindowType.Dialog)
            and not watched.property("_title_bar_styled")
        ):
            watched.setProperty("_title_bar_styled", True)
            style_title_bar(watched)
        return False
