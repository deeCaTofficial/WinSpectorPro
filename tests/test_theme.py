"""Оформление: рамка фокуса появляется только при работе с клавиатуры."""

import pytest

pytest.importorskip("PyQt6")
pytestmark = [pytest.mark.gui]

from PyQt6.QtCore import QEvent, Qt  # noqa: E402
from PyQt6.QtGui import QFocusEvent  # noqa: E402
from PyQt6.QtWidgets import QApplication, QLineEdit, QPushButton  # noqa: E402

from winspector.gui import theme  # noqa: E402


@pytest.fixture
def focus_ring():
    app = QApplication.instance() or QApplication([])
    ring = theme._FocusRing(app)
    app.installEventFilter(ring)
    yield app
    app.removeEventFilter(ring)


def _focus(app, widget, reason):
    app.sendEvent(widget, QFocusEvent(QEvent.Type.FocusIn, reason))


@pytest.mark.parametrize(
    "reason",
    [Qt.FocusReason.TabFocusReason, Qt.FocusReason.BacktabFocusReason],
)
def test_keyboard_focus_shows_ring(focus_ring, reason):
    button = QPushButton()
    _focus(focus_ring, button, reason)
    assert button.property("focusRing") is True


@pytest.mark.parametrize(
    "reason",
    [
        Qt.FocusReason.MouseFocusReason,
        # Alt+Tab и возврат фокуса после закрытия диалога.
        Qt.FocusReason.ActiveWindowFocusReason,
        Qt.FocusReason.OtherFocusReason,
    ],
)
def test_other_focus_hides_ring(focus_ring, reason):
    button = QPushButton()
    _focus(focus_ring, button, Qt.FocusReason.TabFocusReason)
    _focus(focus_ring, button, reason)
    assert button.property("focusRing") is False


def test_losing_focus_hides_ring(focus_ring):
    button = QPushButton()
    _focus(focus_ring, button, Qt.FocusReason.TabFocusReason)
    focus_ring.sendEvent(
        button, QFocusEvent(QEvent.Type.FocusOut, Qt.FocusReason.ActiveWindowFocusReason)
    )
    assert button.property("focusRing") is False


def test_text_fields_are_left_alone(focus_ring):
    field = QLineEdit()
    _focus(focus_ring, field, Qt.FocusReason.MouseFocusReason)
    assert field.property("focusRing") is None


def test_scaled_pixmap_uses_large_enough_ico_frame(qapp, tmp_path):
    """Из многоразмерного .ico берётся крупный кадр, а не растянутый 16 px."""
    from PIL import Image

    path = tmp_path / "app.ico"
    small = Image.new("RGBA", (16, 16), (255, 0, 0, 255))
    large = Image.new("RGBA", (256, 256), (0, 0, 255, 255))
    large.save(path, format="ICO", sizes=[(16, 16), (256, 256)], append_images=[small])

    pixmap = theme.scaled_pixmap(path, 64, 1.0)

    assert pixmap.width() == 64
    assert pixmap.toImage().pixelColor(32, 32).blue() == 255
