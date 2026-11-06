"""Light glass theme: measured contrast, accessibility policy, animation lifecycle, QSS validity."""

import re

import pytest
from PySide6.QtCore import QEvent, QRectF, QtMsgType, qInstallMessageHandler
from PySide6.QtGui import QColor, QImage, QPainter, QPalette
from PySide6.QtWidgets import QApplication, QLabel, QWidget

from interview_assistant.history.store import HistoryStore
from interview_assistant.ui import motion, surfaces, theme, tokens
from interview_assistant.ui.account_screen import AccountScreen
from interview_assistant.ui.overlay import OverlayWindow
from interview_assistant.ui.start_screen import StartScreen
from interview_assistant.ui.surfaces import GlassBackdrop, Panel, surface_color
from interview_assistant.ui.theme import STYLE, apply_theme, window_style


def luminance(colour: QColor) -> float:
    def lin(v: float) -> float:
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4

    return (
        0.2126 * lin(colour.redF()) + 0.7152 * lin(colour.greenF()) + 0.0722 * lin(colour.blueF())
    )


def contrast(a: QColor, b: QColor) -> float:
    la, lb = luminance(a), luminance(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def over(top: QColor, bottom: QColor) -> QColor:
    a = top.alphaF()
    return QColor.fromRgbF(
        top.redF() * a + bottom.redF() * (1 - a),
        top.greenF() * a + bottom.greenF() * (1 - a),
        top.blueF() * a + bottom.blueF() * (1 - a),
    )


# What can sit behind a glass surface inside a workspace window: the base and each mesh colour.
MESH_BEHIND = [QColor(tokens.BASE), *(QColor(c) for c in tokens.MESH)]
TEXT = {
    "ink": tokens.INK,
    "muted text": tokens.MUTED_TEXT,
    "blue": tokens.BLUE,
    "blue dark": tokens.BLUE_DARK,
    "ok": tokens.OK,
    "warn": tokens.WARN,
    "error": tokens.ERROR,
}
WORKSPACE_ROLES = ("panel", "header", "sidebar", "card")


@pytest.mark.parametrize("role", WORKSPACE_ROLES)
@pytest.mark.parametrize("name", sorted(TEXT))
def test_text_meets_aa_on_every_workspace_surface_over_every_mesh_colour(role, name):
    fill = surface_color(role, motion.MotionPolicy())
    for behind in MESH_BEHIND:
        assert contrast(QColor(TEXT[name]), over(fill, behind)) >= 4.5, (name, role, behind.name())


@pytest.mark.parametrize("name", sorted(TEXT))
def test_overlay_text_meets_aa_over_any_desktop(name):
    fill = surface_color("overlay", motion.MotionPolicy())
    for behind in (QColor("black"), QColor("white"), QColor("#ff0000"), QColor("#0000ff")):
        assert contrast(QColor(TEXT[name]), over(fill, behind)) >= 4.5, (name, behind.name())


def test_requested_muted_colour_would_fail_so_text_uses_the_darker_token():
    panel = over(surface_color("panel", motion.MotionPolicy()), QColor(tokens.MESH[1]))
    assert contrast(QColor("#767b93"), panel) < 4.5  # the brief's muted is for icons only
    assert contrast(QColor(tokens.MUTED_TEXT), panel) >= 4.5


@pytest.mark.parametrize(
    ("foreground", "background", "minimum"),
    [
        (tokens.WHITE, tokens.BLUE, 4.5),  # primary buttons
        (tokens.WHITE, tokens.BLUE_DARK, 4.5),
        (tokens.WHITE, tokens.RECORDING, 4.5),  # End / recording / presentation banner
        (tokens.WHITE, tokens.INCOGNITO, 4.4),  # existing indicator colour, bold label
        (tokens.DISABLED_TEXT, tokens.SOLID_PANEL, 3.0),  # disabled text is exempt from AA
    ],
)
def test_filled_controls_and_indicators_are_readable(foreground, background, minimum):
    assert contrast(QColor(foreground), QColor(background)) >= minimum


def test_blue_rim_and_controls_have_non_text_contrast_against_the_panel():
    panel = over(surface_color("panel", motion.MotionPolicy()), QColor(tokens.BASE))
    assert contrast(QColor(tokens.BLUE), panel) >= 3.0  # rim, focus ring, checked toggle


def test_reduced_transparency_makes_every_surface_opaque():
    with motion.override(reduce_transparency=True):
        for role in ("panel", "header", "sidebar", "card", "overlay"):
            assert surface_color(role).alpha() == 255
    for role in ("panel", "header", "sidebar", "card", "overlay"):
        assert surface_color(role, motion.MotionPolicy()).alpha() < 255


def test_overlay_is_more_opaque_than_workspace_panels():
    assert tokens.ALPHA_OVERLAY > tokens.ALPHA_PANEL


def test_high_contrast_uses_system_colours_not_the_glass_stylesheet(qapp):
    previous_style = qapp.styleSheet()
    try:
        with motion.override(high_contrast=True):
            assert window_style() == ""
            apply_theme(qapp)
            assert qapp.styleSheet() == ""
        apply_theme(qapp)
        assert qapp.styleSheet() == STYLE
    finally:
        qapp.setStyleSheet(previous_style)


def test_requested_radii_hierarchy_and_font_family_are_preserved():
    assert (tokens.RADIUS_PANEL, tokens.RADIUS_HEADER, tokens.RADIUS_CARD) == (26, 22, 18)
    assert tokens.RADIUS_CONTROL == 12
    assert "font-family: 'Segoe UI'" in STYLE  # fonts are not part of the redesign
    assert f"border-radius: {tokens.RADIUS_CONTROL}px" in STYLE


def test_stylesheet_has_no_parse_errors(qtbot):
    messages: list[str] = []

    def handler(kind, _context, message):
        if kind in (QtMsgType.QtWarningMsg, QtMsgType.QtCriticalMsg):
            messages.append(message)

    previous = qInstallMessageHandler(handler)
    try:
        widget = QWidget()
        qtbot.addWidget(widget)
        widget.setStyleSheet(STYLE)
        widget.show()
        QApplication.processEvents()
    finally:
        qInstallMessageHandler(previous)
    assert not [m for m in messages if "stylesheet" in m.lower() or "parse" in m.lower()]


# ---- rendered output ----


def lightest_pixel(image: QImage) -> QColor:
    best, best_l = QColor(), -1.0
    for y in range(0, image.height(), 2):
        for x in range(0, image.width(), 2):
            colour = image.pixelColor(x, y)
            if luminance(colour) > best_l:
                best, best_l = colour, luminance(colour)
    return best


def test_rendered_workspace_text_is_readable_on_its_actual_background(qtbot):
    store = HistoryStore(":memory:")
    store.create_interview_session(company="Northwind", role_title="Engineer")
    screen = StartScreen(store, None, lambda d: None, lambda: None, lambda: None)
    qtbot.addWidget(screen)
    screen.show()
    QApplication.processEvents()
    pixmap = screen.grab()
    image = pixmap.toImage()
    ratio = pixmap.devicePixelRatio()
    checked = 0
    for label in screen.findChildren(QLabel):
        if not (label.isVisible() and label.text()) or label.objectName() not in {"hint", "title"}:
            continue
        top_left = label.mapTo(screen, label.rect().topLeft())
        region = image.copy(
            round(top_left.x() * ratio),
            round(top_left.y() * ratio),
            round(label.width() * ratio),
            round(label.height() * ratio),
        )
        background = lightest_pixel(region)
        colour = QColor(tokens.INK if label.objectName() == "title" else tokens.MUTED_TEXT)
        assert contrast(colour, background) >= 4.5, label.text()
        checked += 1
    assert checked >= 2


# ---- animation lifecycle ----


def make_backdrop(qtbot, *, animated=True, panels=None):
    window = QWidget()
    qtbot.addWidget(window)
    backdrop = GlassBackdrop(window, panels or (lambda: []), animated=animated)
    return window, backdrop


def test_tick_rate_stays_below_the_15fps_cap():
    assert 1000 / surfaces.TICK_MS <= 15


def test_unanimated_backdrop_never_starts_a_timer(qtbot):
    with motion.override(reduce_motion=False, high_contrast=False):
        window, backdrop = make_backdrop(qtbot, animated=False)
        window.show()
        backdrop.sync()
        assert not backdrop.running


def test_close_stops_the_timer(qtbot):
    with motion.override(reduce_motion=False, high_contrast=False):
        window, backdrop = make_backdrop(qtbot)
        window.show()
        window.activateWindow()
        qtbot.waitUntil(lambda: backdrop.running, timeout=2000)
        window.close()
        qtbot.waitUntil(lambda: not backdrop.running)


def test_policy_change_while_running_stops_without_new_events(qtbot):
    with motion.override(reduce_motion=False, high_contrast=False):
        window, backdrop = make_backdrop(qtbot)
        window.show()
        window.activateWindow()
        qtbot.waitUntil(lambda: backdrop.running, timeout=2000)
        motion.set_override(motion.MotionPolicy(reduce_motion=True))
        qtbot.waitUntil(lambda: not backdrop.running, timeout=2000)  # next tick re-checks policy


def test_overlay_has_no_animation_machinery(qtbot, tmp_path):
    from PySide6.QtCore import QSettings

    overlay = OverlayWindow(
        settings=QSettings(str(tmp_path / "ui.ini"), QSettings.Format.IniFormat)
    )
    qtbot.addWidget(overlay)
    assert not overlay.findChildren(GlassBackdrop)


def test_workspace_windows_are_the_animated_surfaces(qtbot):
    store = HistoryStore(":memory:")
    screen = StartScreen(store, None, lambda d: None, lambda: None, lambda: None)
    qtbot.addWidget(screen)
    login = AccountScreen()
    qtbot.addWidget(login)
    assert screen.findChildren(GlassBackdrop) and login.findChildren(GlassBackdrop)


def test_mesh_is_cached_until_the_window_is_resized(qtbot):
    window, backdrop = make_backdrop(
        qtbot,
        panels=lambda: [Panel(window.rect().toRectF(), tokens.RADIUS_PANEL, "panel", rim=True)],
    )
    window.resize(300, 200)
    image = QImage(300, 200, QImage.Format.Format_ARGB32_Premultiplied)
    painter = QPainter(image)
    backdrop.paint(painter)
    first = backdrop._mesh
    backdrop.paint(painter)
    assert backdrop._mesh is first
    window.resize(320, 200)
    painter.end()
    image = QImage(320, 200, QImage.Format.Format_ARGB32_Premultiplied)
    painter = QPainter(image)
    backdrop.paint(painter)
    painter.end()
    assert backdrop._mesh is not first


def test_reduced_motion_and_paused_windows_paint_the_same_static_frame(qtbot):
    window, backdrop = make_backdrop(qtbot)
    window.resize(300, 200)

    def frame() -> QImage:
        image = QImage(300, 200, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(0)
        painter = QPainter(image)
        backdrop.paint(painter)
        painter.end()
        return image

    with motion.override(reduce_motion=True):
        backdrop.advance(9.0)
        moved = frame()
        backdrop.advance(11.0)
        assert frame() == moved  # reduced motion ignores the animation clock entirely
    with motion.override(reduce_motion=False, high_contrast=False):
        assert frame() != moved  # animations allowed: the clock moves the rim and mesh


# ---- accessibility regressions found in review ----


def test_high_contrast_panels_use_the_system_palette_not_hardcoded_white(qapp):
    original = QPalette(qapp.palette())
    system = QPalette(original)
    system.setColor(QPalette.ColorRole.Window, QColor("black"))
    system.setColor(QPalette.ColorRole.WindowText, QColor("white"))
    qapp.setPalette(system)
    try:
        with motion.override(high_contrast=True):
            image = QImage(120, 80, QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(0)
            painter = QPainter(image)
            surfaces.paint_panel(painter, Panel(QRectF(0, 0, 120, 80), 20, "panel"))
            painter.end()
        assert image.pixelColor(60, 40) == QColor("black")  # white OS text stays readable on it
    finally:
        qapp.setPalette(original)


def test_entering_high_contrast_discards_the_glass_palette(qapp):
    previous_style, previous_palette = qapp.styleSheet(), QPalette(qapp.palette())
    try:
        apply_theme(qapp)
        assert qapp.palette().color(QPalette.ColorRole.Window) == QColor(tokens.BASE)
        with motion.override(high_contrast=True):
            apply_theme(qapp)
            assert qapp.palette().color(QPalette.ColorRole.Window) != QColor(tokens.BASE)
            assert theme.status_color(tokens.WARN) == "palette(window-text)"
        assert theme.status_color(tokens.WARN) == tokens.WARN
    finally:
        qapp.setStyleSheet(previous_style)
        qapp.setPalette(previous_palette)


def test_reduced_transparency_stylesheet_has_no_translucent_white():
    translucent = re.compile(r"rgba\(255, ?255, ?255, ?(\d+)\)")
    assert any(int(a) < 255 for a in translucent.findall(STYLE))
    assert all(int(a) == 255 for a in translucent.findall(theme.OPAQUE_STYLE))
    with motion.override(reduce_transparency=True):
        assert window_style() == theme.OPAQUE_STYLE
        assert theme.white(150) == "rgba(255, 255, 255, 255)"
    assert theme.white(150) == "rgba(255, 255, 255, 150)"


def frame_of(backdrop, size=(300, 200)) -> QImage:
    image = QImage(*size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(0)
    painter = QPainter(image)
    backdrop.paint(painter)
    painter.end()
    return image


def test_reduced_transparency_keeps_the_mesh_still(qtbot):
    window, backdrop = make_backdrop(qtbot)
    window.resize(300, 200)
    with motion.override(reduce_motion=False, high_contrast=False, reduce_transparency=True):
        still = frame_of(backdrop)
        backdrop.advance(9.0)
        # The rim may still turn, so compare a mesh-only region away from the panel edges.
        assert frame_of(backdrop).pixelColor(150, 100) == still.pixelColor(150, 100)
    with motion.override(reduce_motion=False, high_contrast=False, reduce_transparency=False):
        before = frame_of(backdrop).pixelColor(150, 100)
        backdrop.advance(9.0)
        assert frame_of(backdrop).pixelColor(150, 100) != before


def test_re_enabling_motion_restarts_a_stopped_backdrop_on_a_theme_change(qtbot):
    with motion.override(reduce_motion=True, high_contrast=False):
        window, backdrop = make_backdrop(qtbot)
        window.show()
        window.activateWindow()
        qtbot.wait(150)
        assert not backdrop.running
        motion.set_override(motion.MotionPolicy(reduce_motion=False))
        QApplication.sendEvent(window, QEvent(QEvent.Type.ThemeChange))
        qtbot.waitUntil(lambda: backdrop.running, timeout=2000)


def test_toggle_switch_jumps_when_motion_is_reduced(qtbot):
    from interview_assistant.ui.controls import ToggleSwitch

    toggle = ToggleSwitch()
    qtbot.addWidget(toggle)
    toggle.show()
    with motion.override(reduce_motion=True):
        toggle.setChecked(True)
        assert toggle._position == 1.0
        assert toggle._animation.state() != toggle._animation.State.Running
    with motion.override(reduce_motion=False, high_contrast=False):
        toggle.setChecked(False)
        assert toggle._animation.state() == toggle._animation.State.Running


def test_practice_page_transition_is_skipped_when_motion_is_reduced(qtbot, tmp_path):
    from interview_assistant.app.practice_library import PracticeLibrary
    from interview_assistant.ui.practice_page import PracticePage

    page = PracticePage(PracticeLibrary(tmp_path / "qa.json", lambda: True))
    qtbot.addWidget(page)
    page.show()
    with motion.override(reduce_motion=True):
        page._show(page.stack.currentWidget())
        assert page._transition.state() != page._transition.State.Running
        assert page._opacity.opacity() == 1.0
    with motion.override(reduce_motion=False, high_contrast=False):
        page._show(page.stack.currentWidget())
        assert page._transition.state() == page._transition.State.Running
