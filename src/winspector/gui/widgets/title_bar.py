"""
Своя полоса заголовка главного окна.

В системный заголовок не поставить кнопки приложения, и он отделяет окно
от фона полосой другого цвета. Эта полоса прозрачная — под ней та же сетка
точек, что и под содержимым, — и в ней помещаются название, кнопки
приложения (шестерёнка настроек) и кнопки окна.

Перетаскивание, привязку к краям экрана, двойной щелчок и меню окна
по-прежнему делает сама Windows: `frameless.py` сообщает ей, где у окна
заголовок и где кнопка «Развернуть». Без Windows (например, в тестах)
окно двигается через `startSystemMove`.
"""

from __future__ import annotations

from PyQt6.QtCore import QEvent, QObject, QPoint, Qt, QTimer
from PyQt6.QtGui import QColor, QCursor, QFont, QPainter
from PyQt6.QtWidgets import QAbstractButton, QHBoxLayout, QLabel, QWidget

from ..language import localize
from ..theme import DIM, TEXT, icon_font

HEIGHT = 40
_BUTTON_WIDTH = 46
_GLYPH_SIZE = 10

GLYPH_MINIMIZE = "\ue921"
GLYPH_MAXIMIZE = "\ue922"
GLYPH_RESTORE = "\ue923"
GLYPH_CLOSE = "\ue8bb"

# Подсветка как у кнопок окна в тёмной теме Windows 11.
_HOVER = QColor(255, 255, 255, 15)
_PRESSED = QColor(255, 255, 255, 10)
_CLOSE_HOVER = QColor("#C42B1C")
_CLOSE_PRESSED = QColor(196, 43, 28, 230)

# Пока кнопкой «Развернуть» управляет Windows, Qt не сообщает об уходе
# курсора: подсветку снимает проверка положения курсора.
_NATIVE_HOVER_CHECK_MS = 80


class CaptionButton(QAbstractButton):
    """Кнопка окна: свернуть, развернуть или закрыть."""

    def __init__(self, glyph: str, *, close: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._glyph = glyph
        self._close = close
        self._window_active = True
        # Состояние от Windows: над «Развернуть» мышью управляет система.
        self._native_hover = False
        self._native_pressed = False
        self.setFixedSize(_BUTTON_WIDTH, HEIGHT)
        # Кнопки окна в Windows не участвуют в переходе по Tab.
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    @property
    def glyph(self) -> str:
        return self._glyph

    def set_glyph(self, glyph: str) -> None:
        self._glyph = glyph
        self.update()

    def set_window_active(self, active: bool) -> None:
        self._window_active = active
        self.update()

    @property
    def native_pressed(self) -> bool:
        return self._native_pressed

    def set_native_state(self, *, hover: bool, pressed: bool) -> None:
        if (hover, pressed) != (self._native_hover, self._native_pressed):
            self._native_hover, self._native_pressed = hover, pressed
            self.update()

    def enterEvent(self, event) -> None:
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event) -> None:
        super().leaveEvent(event)
        self.update()

    def paintEvent(self, event) -> None:
        hovered = self.underMouse() or self._native_hover
        pressed = self.isDown() or self._native_pressed
        painter = QPainter(self)
        if pressed or hovered:
            if self._close:
                painter.fillRect(self.rect(), _CLOSE_PRESSED if pressed else _CLOSE_HOVER)
            else:
                painter.fillRect(self.rect(), _PRESSED if pressed else _HOVER)
        if self._close and (pressed or hovered):
            color = QColor("#FFFFFF")
        else:
            # У неактивного окна значки бледнеют, как у системных кнопок.
            color = QColor(TEXT if self._window_active or hovered else DIM)
        font = icon_font(_GLYPH_SIZE)
        if font is not None:
            # Без цветной каймы ClearType: тонкие значки с ней рябят.
            font.setStyleStrategy(QFont.StyleStrategy.NoSubpixelAntialias)
            painter.setFont(font)
            painter.setPen(color)
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._glyph)
        painter.end()


class TitleBar(QWidget):
    """Название, кнопки приложения и кнопки окна в одной прозрачной полосе."""

    def __init__(self, window: QWidget, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._window = window
        self.setFixedHeight(HEIGHT)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 0, 0, 0)
        layout.setSpacing(0)
        self.title = QLabel(window.windowTitle())
        self.title.setObjectName("WindowTitle")
        layout.addWidget(self.title)
        layout.addSpacing(12)
        # Слева, после названия, — уведомления; справа, у кнопок окна, —
        # кнопки приложения.
        self.leading = QHBoxLayout()
        self.leading.setSpacing(8)
        layout.addLayout(self.leading)
        layout.addStretch()
        self.trailing = QHBoxLayout()
        self.trailing.setSpacing(4)
        layout.addLayout(self.trailing)
        layout.addSpacing(8)

        self.minimize_button = CaptionButton(GLYPH_MINIMIZE)
        self.maximize_button = CaptionButton(GLYPH_MAXIMIZE)
        self.close_button = CaptionButton(GLYPH_CLOSE, close=True)
        for button in (self.minimize_button, self.maximize_button, self.close_button):
            layout.addWidget(button)
        self.minimize_button.clicked.connect(window.showMinimized)
        self.maximize_button.clicked.connect(self.toggle_maximized)
        self.close_button.clicked.connect(window.close)

        window.windowTitleChanged.connect(self.title.setText)
        window.installEventFilter(self)
        self._native_check = QTimer(self)
        self._native_check.setInterval(_NATIVE_HOVER_CHECK_MS)
        self._native_check.timeout.connect(self._check_native_hover)
        self.retranslate("ru")
        self.sync_window_state()

    def retranslate(self, language: str) -> None:
        self._language = language
        self.minimize_button.setToolTip(localize(language, "Свернуть", "Minimize"))
        self.close_button.setToolTip(localize(language, "Закрыть", "Close"))
        self.sync_window_state()

    def toggle_maximized(self) -> None:
        if self._window.isMaximized():
            self._window.showNormal()
        else:
            self._window.showMaximized()

    def sync_window_state(self) -> None:
        maximized = self._window.isMaximized()
        self.maximize_button.set_glyph(GLYPH_RESTORE if maximized else GLYPH_MAXIMIZE)
        self.maximize_button.setToolTip(
            localize(self._language, "Свернуть в окно", "Restore down")
            if maximized
            else localize(self._language, "Развернуть", "Maximize")
        )

    def set_window_active(self, active: bool) -> None:
        self.title.setProperty("inactive", not active)
        style = self.title.style()
        if style is not None:
            style.unpolish(self.title)
            style.polish(self.title)
        for button in (self.minimize_button, self.maximize_button, self.close_button):
            button.set_window_active(active)

    def eventFilter(self, watched: QObject | None, event: QEvent | None) -> bool:
        if watched is self._window and event is not None:
            if event.type() == QEvent.Type.WindowStateChange:
                self.sync_window_state()
            elif event.type() == QEvent.Type.ActivationChange:
                self.set_window_active(self._window.isActiveWindow())
        return False

    # --- Что под курсором: спрашивает frameless.py --------------------------

    def is_control_at(self, point: QPoint) -> bool:
        """Есть ли кнопка в точке `point` (в координатах полосы)."""
        child = self.childAt(point)
        while child is not None and child is not self:
            if isinstance(child, QAbstractButton):
                return True
            child = child.parentWidget()
        return False

    def is_maximize_at(self, point: QPoint) -> bool:
        return self.maximize_button.geometry().contains(point)

    def set_maximize_native_state(self, *, hover: bool, pressed: bool = False) -> None:
        """Подсветка «Развернуть», пока кнопкой управляет Windows (меню привязки)."""
        self.maximize_button.set_native_state(hover=hover, pressed=pressed)
        if hover or pressed:
            self._native_check.start()
        else:
            self._native_check.stop()

    def _check_native_hover(self) -> None:
        if not self.is_maximize_at(self.mapFromGlobal(QCursor.pos())):
            self.set_maximize_native_state(hover=False)

    # --- Без Windows: перетаскивание силами Qt -------------------------------

    def mousePressEvent(self, event) -> None:
        handle = self._window.windowHandle()
        if event.button() == Qt.MouseButton.LeftButton and handle is not None:
            handle.startSystemMove()
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.toggle_maximized()
        super().mouseDoubleClickEvent(event)
