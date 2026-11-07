"""Independent behavioral checks for glass animation accessibility and lifecycle."""

import pytest
from PySide6.QtWidgets import QApplication, QWidget

from interview_assistant.ui import motion
from interview_assistant.ui.surfaces import GlassBackdrop, surface_color


@pytest.fixture
def animated_window(qtbot):
    with motion.override(reduce_motion=False, high_contrast=False):
        window = QWidget()
        qtbot.addWidget(window)
        backdrop = GlassBackdrop(window, lambda: [], animated=True)
        window.show()
        QApplication.setActiveWindow(window)
        backdrop.sync()
        assert backdrop.running
        yield window, backdrop
        backdrop.stop()


def test_hidden_window_stops_background_animation(animated_window, qtbot):
    window, backdrop = animated_window
    window.hide()
    qtbot.waitUntil(lambda: not backdrop.running)


def test_minimized_window_stops_background_animation(animated_window, qtbot):
    window, backdrop = animated_window
    window.showMinimized()
    qtbot.waitUntil(lambda: not backdrop.running)


def test_inactive_window_stops_background_animation(animated_window, qtbot):
    _, backdrop = animated_window
    other = QWidget()
    qtbot.addWidget(other)
    other.show()
    QApplication.setActiveWindow(other)
    qtbot.waitUntil(lambda: not backdrop.running)


@pytest.mark.parametrize("flag", ["reduce_motion", "high_contrast"])
def test_os_reduction_stops_running_timer(animated_window, qtbot, flag):
    _, backdrop = animated_window
    with motion.override(**{flag: True}):
        qtbot.waitUntil(lambda: not backdrop.running)


def test_static_surface_never_starts_timer(qtbot):
    with motion.override(reduce_motion=False, high_contrast=False):
        window = QWidget()
        qtbot.addWidget(window)
        backdrop = GlassBackdrop(window, lambda: [], animated=False)
        window.show()
        QApplication.setActiveWindow(window)
        backdrop.sync()
        assert not backdrop.running


@pytest.mark.parametrize("role", ["panel", "header", "sidebar", "card", "overlay"])
@pytest.mark.parametrize("flag", ["reduce_transparency", "high_contrast"])
def test_os_opacity_preference_removes_transparency(role, flag):
    policy = motion.MotionPolicy(**{flag: True})
    assert surface_color(role, policy).alpha() == 255
