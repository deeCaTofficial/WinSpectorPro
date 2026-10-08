"""Свой заголовок окна: кнопки окна и ответы Windows о том, что под курсором."""

import pytest

pytest.importorskip("PyQt6")
pytestmark = [pytest.mark.gui]

from PyQt6.QtCore import QPoint  # noqa: E402
from PyQt6.QtWidgets import QApplication, QPushButton, QWidget  # noqa: E402

from winspector.gui import frameless  # noqa: E402
from winspector.gui.frameless import hit_test  # noqa: E402
from winspector.gui.widgets.title_bar import (  # noqa: E402
    GLYPH_MAXIMIZE,
    GLYPH_RESTORE,
    TitleBar,
)

W, H, CAPTION = 900, 600, 40


def _hit(x, y, *, maximized=False, on_control=False, on_maximize=False):
    return hit_test(
        x,
        y,
        W,
        H,
        maximized=maximized,
        caption_height=CAPTION,
        on_control=on_control,
        on_maximize=on_maximize,
    )


@pytest.mark.parametrize(
    ("x", "y", "expected"),
    [
        (0, 300, frameless.HTLEFT),
        (W - 1, 300, frameless.HTRIGHT),
        (450, 0, frameless.HTTOP),
        (450, H - 1, frameless.HTBOTTOM),
        (0, 0, frameless.HTTOPLEFT),
        # Угол ловится не только в квадрате у самого края.
        (10, 1, frameless.HTTOPLEFT),
        (W - 1, 12, frameless.HTTOPRIGHT),
        (12, H - 1, frameless.HTBOTTOMLEFT),
        (W - 2, H - 2, frameless.HTBOTTOMRIGHT),
        (450, 20, frameless.HTCAPTION),
        (450, 300, frameless.HTCLIENT),
    ],
)
def test_hit_test_edges_caption_and_content(x, y, expected):
    assert _hit(x, y) == expected


def test_hit_test_buttons_in_caption():
    assert _hit(800, 20, on_control=True) == frameless.HTCLIENT
    # Над «Развернуть» Windows 11 показывает макеты привязки.
    assert _hit(820, 20, on_control=True, on_maximize=True) == frameless.HTMAXBUTTON


def test_maximized_window_has_no_resize_edges():
    assert _hit(0, 300, maximized=True) == frameless.HTCLIENT
    assert _hit(450, 0, maximized=True) == frameless.HTCAPTION


@pytest.fixture
def bar(qapp):
    window = QWidget()
    window.setWindowTitle("WinSpector Pro")
    window.resize(W, H)
    title_bar = TitleBar(window, window)
    title_bar.trailing.addWidget(QPushButton("⚙"))
    title_bar.resize(W, CAPTION)
    title_bar.layout().activate()
    yield window, title_bar
    window.close()


def test_title_follows_window_title(bar):
    window, title_bar = bar
    assert title_bar.title.text() == "WinSpector Pro"
    window.setWindowTitle("Другое")
    assert title_bar.title.text() == "Другое"


def test_maximize_glyph_and_tooltip_follow_window_state(bar):
    window, title_bar = bar
    window.show()
    title_bar.toggle_maximized()
    QApplication.processEvents()
    assert window.isMaximized()
    assert title_bar.maximize_button.glyph == GLYPH_RESTORE
    title_bar.retranslate("en")
    assert title_bar.maximize_button.toolTip() == "Restore down"
    title_bar.toggle_maximized()
    QApplication.processEvents()
    assert title_bar.maximize_button.glyph == GLYPH_MAXIMIZE
    assert title_bar.maximize_button.toolTip() == "Maximize"


def test_controls_are_found_and_empty_space_is_caption(bar):
    _, title_bar = bar
    gear = title_bar.trailing.itemAt(0).widget()
    assert title_bar.is_control_at(gear.geometry().center())
    assert title_bar.is_control_at(title_bar.close_button.geometry().center())
    # Пустое место и название — заголовок, за него окно перетаскивают.
    assert not title_bar.is_control_at(QPoint(W // 2, CAPTION // 2))
    assert not title_bar.is_control_at(title_bar.title.geometry().center())
    assert title_bar.is_maximize_at(title_bar.maximize_button.geometry().center())
    assert not title_bar.is_maximize_at(title_bar.close_button.geometry().center())


def test_native_maximize_hover_clears_when_cursor_leaves(bar, monkeypatch):
    _, title_bar = bar
    title_bar.set_maximize_native_state(hover=True, pressed=True)
    assert title_bar.maximize_button.native_pressed
    monkeypatch.setattr(
        "winspector.gui.widgets.title_bar.QCursor.pos", staticmethod(lambda: QPoint(-500, -500))
    )
    title_bar._check_native_hover()
    assert not title_bar.maximize_button.native_pressed
    assert not title_bar._native_check.isActive()


def test_inactive_window_dims_title(bar):
    _, title_bar = bar
    title_bar.set_window_active(False)
    assert title_bar.title.property("inactive") is True
    title_bar.set_window_active(True)
    assert title_bar.title.property("inactive") is False
