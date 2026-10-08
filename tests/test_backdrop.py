"""Фон главного окна: сетка рисуется, волны идут, анимация уважает настройки Windows."""

import pytest

pytest.importorskip("PyQt6")
pytestmark = [pytest.mark.gui]

from PyQt6.QtGui import QColor  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from winspector.gui import theme  # noqa: E402
from winspector.gui.widgets import backdrop as backdrop_module  # noqa: E402
from winspector.gui.widgets.backdrop import DotGridBackground  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def backdrop(qapp, monkeypatch):
    monkeypatch.setattr(backdrop_module, "animations_enabled", lambda: True)
    widget = DotGridBackground()
    widget.resize(400, 300)
    yield widget
    widget.close()


def _colours(widget) -> set[str]:
    image = widget.grab().toImage()
    assert not image.isNull()
    return {
        image.pixelColor(x, y).name()
        for x in range(image.width())
        for y in range(0, image.height(), 2)
    }


def test_grid_dots_are_drawn_over_the_base_background(backdrop):
    assert _colours(backdrop) - {QColor(theme.BG).name()}, "точки сетки должны быть видны"


def test_waves_start_spread_and_change_the_picture(backdrop):
    before = backdrop.grab().toImage()
    backdrop.advance(backdrop_module._FIRST_WAVE_DELAY + 0.01)
    assert backdrop.waves == [0.0]
    backdrop.advance(0.8)
    assert backdrop.waves[0] > 0
    assert backdrop.grab().toImage() != before


def test_active_mode_sends_waves_sooner_and_faster(backdrop):
    backdrop.set_active(True)
    backdrop.advance(0.31)
    assert backdrop.waves, "во время работы первая волна идёт почти сразу"
    backdrop.advance(1.0)
    assert backdrop.waves[0] == pytest.approx(backdrop_module._ACTIVE_SPEED * 1.0)


def test_waves_leave_the_window_and_are_dropped(backdrop):
    backdrop.waves = [10_000.0]
    backdrop.advance(0.1)
    assert backdrop.waves == []


def test_runs_only_while_visible_and_not_paused(backdrop, qapp):
    assert not backdrop.running, "скрытый виджет не тратит кадры"
    backdrop.show()
    qapp.processEvents()
    assert backdrop.running
    backdrop.set_paused(True)
    assert not backdrop.running
    backdrop.set_paused(False)
    assert backdrop.running
    backdrop.hide()
    assert not backdrop.running


def test_respects_disabled_windows_animations(qapp, monkeypatch):
    monkeypatch.setattr(backdrop_module, "animations_enabled", lambda: False)
    widget = DotGridBackground()
    try:
        widget.show()
        qapp.processEvents()
        assert not widget.running
        assert not widget.grab().toImage().isNull(), "статичная сетка всё равно рисуется"
    finally:
        widget.close()
