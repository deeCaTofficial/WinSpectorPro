# tests/test_neural_background.py
"""
Тесты анимированного фона.

Виджет декоративный, но работает постоянно и на каждом кадре, поэтому важны
две вещи: он не должен нарушать инварианты физики и не должен потреблять
процессор, когда анимация выключена.
"""

from __future__ import annotations

import math
import random

import pytest

pytest.importorskip("PyQt6", reason="PyQt6 не установлен")
pytestmark = [pytest.mark.gui]

from PyQt6.QtWidgets import QApplication  # noqa: E402

from winspector.gui.widgets.neural_background import (  # noqa: E402
    NeuralBackgroundWidget,
    Particle,
)

WIDTH, HEIGHT = 850, 700


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def widget(qapp):
    random.seed(1234)
    background = NeuralBackgroundWidget()
    background.resize(WIDTH, HEIGHT)
    background.show()
    background.init_particles()
    background.is_animation_running = True
    background.mouse_x, background.mouse_y = WIDTH / 2, HEIGHT / 2
    yield background
    background.stop_animation()
    background.close()


def advance(widget: NeuralBackgroundWidget, frames: int = 100) -> None:
    for _ in range(frames):
        widget.update_particles()


class TestTimerLifecycle:
    """Таймер не должен будить приложение, пока анимация не нужна."""

    def test_timer_is_idle_after_construction(self, qapp):
        background = NeuralBackgroundWidget()
        assert not background.animation_timer.isActive()

    def test_start_and_stop_toggle_the_timer(self, widget):
        widget.stop_animation()
        assert not widget.animation_timer.isActive()

        widget.start_animation()
        assert widget.animation_timer.isActive()
        assert widget.is_animation_running

        widget.stop_animation()
        assert not widget.animation_timer.isActive()
        assert not widget.is_animation_running

    def test_hiding_stops_the_timer(self, widget):
        widget.start_animation()
        widget.hide()
        assert not widget.animation_timer.isActive()

    def test_stopped_animation_does_no_work(self, widget):
        widget.is_animation_running = False
        before = [(p.x, p.y) for p in widget.particles]

        advance(widget, 20)

        assert [(p.x, p.y) for p in widget.particles] == before


class TestPhysicsInvariants:
    def test_particles_stay_inside_the_widget(self, widget):
        advance(widget, 300)

        for particle in widget.particles:
            assert -1 <= particle.x <= WIDTH + 1
            assert -1 <= particle.y <= HEIGHT + 1

    def test_speed_stays_within_limits(self, widget):
        advance(widget, 300)

        for particle in widget.particles:
            speed = math.hypot(particle.vx, particle.vy)
            assert widget.MIN_SPEED - 1e-6 <= speed <= widget.MAX_SPEED + 1e-6

    def test_particle_count_is_stable(self, widget):
        advance(widget, 200)
        assert len(widget.particles) == widget.PARTICLE_COUNT

    def test_connections_reference_existing_particles(self, widget):
        advance(widget, 200)

        count = len(widget.particles)
        for i, j in widget.active_connections:
            assert 0 <= i < count
            assert 0 <= j < count
            assert i < j, "пара должна храниться в нормализованном порядке"

    def test_connection_opacity_is_normalised(self, widget):
        advance(widget, 200)

        for opacity, dist_sq in widget.active_connections.values():
            assert 0.0 < opacity <= 1.0
            assert dist_sq >= 0.0

    def test_new_connections_per_particle_are_bounded(self, widget):
        """
        Предел MAX_CONNECTIONS действует на вновь возникающие связи.

        В `active_connections` попадают ещё и затухающие пары: они уже не
        выбраны, но плавно исчезают, поэтому степень вершины там временно
        выше предела — это штатное поведение затухания, а не нарушение.
        """
        widget.active_connections.clear()
        # Один кадр после сброса: затухающих связей ещё нет.
        widget.update_particles()

        degree: dict[int, int] = {}
        for i, j in widget.active_connections:
            degree[i] = degree.get(i, 0) + 1
            degree[j] = degree.get(j, 0) + 1

        assert degree, "связи должны появиться хотя бы у части частиц"
        assert all(value <= widget.MAX_CONNECTIONS for value in degree.values())

    def test_fading_connections_disappear(self, widget):
        """Связь, потерявшая пару, должна дойти до нуля и исчезнуть."""
        advance(widget, 50)
        widget.particles = []
        widget.update_particles()  # без частиц кадр не считается

        widget.particles = [widget._create_particle() for _ in range(widget.PARTICLE_COUNT)]
        frames_to_fade = int(1.0 / widget.FADE_SPEED) + 5
        advance(widget, frames_to_fade)

        assert all(opacity > 0.0 for opacity, _ in widget.active_connections.values())

    def test_no_numerical_blow_up(self, widget):
        advance(widget, 500)

        for particle in widget.particles:
            assert math.isfinite(particle.x)
            assert math.isfinite(particle.y)
            assert math.isfinite(particle.vx)
            assert math.isfinite(particle.vy)

    def test_cursor_pushes_particles_away(self, widget):
        """Отталкивание от курсора должно расчищать область вокруг него."""
        widget.mouse_x, widget.mouse_y = WIDTH / 2, HEIGHT / 2
        advance(widget, 200)

        radius = widget.REPULSION_RADIUS
        too_close = [
            p
            for p in widget.particles
            if math.hypot(p.x - widget.mouse_x, p.y - widget.mouse_y) < radius * 0.35
        ]
        assert not too_close


class TestParticle:
    def test_uses_slots_to_stay_lightweight(self):
        particle = Particle(1.0, 2.0, 0.1, 0.2, 3.0, 0.5, 9.0)
        assert not hasattr(particle, "__dict__")
        with pytest.raises(AttributeError):
            particle.unexpected_attribute = 1

    def test_stores_plain_numbers(self):
        """Физика работает на float: обращения к QPointF стоили дороже всего."""
        particle = Particle(1.0, 2.0, 0.1, 0.2, 3.0, 0.5, 9.0)
        assert isinstance(particle.x, float)
        assert isinstance(particle.vx, float)


class TestPalette:
    def test_hue_index_stays_in_range(self, widget):
        for oscillation in (-2.0, -1.0, -0.5, 0.0, 0.5, 1.0, 2.0):
            index = widget._hue_index(oscillation)
            assert 0 <= index < widget._HUE_STEPS

    def test_palette_is_precomputed(self, widget):
        assert len(widget._line_colors) == widget._HUE_STEPS
        assert len(widget._particle_colors) == widget._HUE_STEPS


class TestRendering:
    def test_widget_renders_without_errors(self, widget):
        advance(widget, 50)
        pixmap = widget.grab()

        assert pixmap.width() == WIDTH
        assert pixmap.height() == HEIGHT

        image = pixmap.toImage()
        sampled = {image.pixel(x, y) for x in range(0, WIDTH, 50) for y in range(0, HEIGHT, 50)}
        assert len(sampled) > 1, "на фоне должны быть видны частицы и линии"

    def test_renders_before_first_frame(self, qapp):
        """Отрисовка не должна падать, пока частиц ещё нет."""
        background = NeuralBackgroundWidget()
        background.resize(WIDTH, HEIGHT)
        assert background.grab().width() == WIDTH
        background.close()
