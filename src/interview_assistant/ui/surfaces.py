"""Painted light-glass surfaces: pastel mesh, translucent panels and the blue rim.

Qt Widgets cannot blur what is behind a window, so panels are translucent white over a mesh that
is painted by the same window. The overlay floats over arbitrary content and therefore uses a
much more opaque fill. Only workspace windows animate; every animation stops when the window is
hidden, minimized or inactive, or when Windows asks for reduced motion.
"""

import math
import time
from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import QEvent, QObject, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import (
    QBrush,
    QColor,
    QConicalGradient,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPalette,
    QPen,
    QPixmap,
    QRadialGradient,
)
from PySide6.QtWidgets import QApplication, QWidget

from interview_assistant.ui import motion, tokens

TICK_MS = 80  # 12.5 fps, below the 15 fps cap
STATIC_RIM_ANGLE = 40.0


def _alpha(role: str) -> int:
    return {
        "panel": tokens.ALPHA_PANEL,
        "header": tokens.ALPHA_HEADER,
        "sidebar": tokens.ALPHA_SIDEBAR,
        "card": tokens.ALPHA_CARD,
        "overlay": tokens.ALPHA_OVERLAY,
    }[role]


def _solid(role: str) -> str:
    return {
        "panel": tokens.SOLID_PANEL,
        "header": tokens.SOLID_PANEL,
        "sidebar": tokens.SOLID_SIDEBAR,
        "card": tokens.SOLID_CARD,
        "overlay": tokens.SOLID_PANEL,
    }[role]


@dataclass(frozen=True)
class Panel:
    rect: QRectF
    radius: float = tokens.RADIUS_PANEL
    role: str = "panel"
    rim: bool = False


def surface_color(role: str, policy: motion.MotionPolicy | None = None) -> QColor:
    """Fill colour for a surface role, opaque when transparency is reduced."""
    policy = policy or motion.current()
    if policy.reduce_transparency or policy.high_contrast:
        return QColor(_solid(role))
    colour = QColor(tokens.SURFACE)
    colour.setAlpha(_alpha(role))
    return colour


def _shadow(alpha: int) -> QColor:
    r, g, b = (int(v) for v in tokens.SHADOW_RGB.split(","))
    return QColor(r, g, b, alpha)


def _edge(alpha: int) -> QColor:
    """The panel outline: bright on light glass, a faint blue-white line on dark glass."""
    colour = QColor(tokens.EDGE)
    colour.setAlpha(min(alpha, tokens.EDGE_ALPHA + 45))
    return colour


def _rim_brush(rect: QRectF, angle: float) -> QBrush:
    gradient = QConicalGradient(rect.center(), angle)
    for stop, colour, alpha in (
        (0.0, tokens.BLUE, 235),
        (0.14, "#6f83ff", 220),
        (0.30, tokens.MESH[2], 150),
        (0.55, tokens.EDGE, 120),
        (0.80, "#8fa0ff", 90),
        (1.0, tokens.BLUE, 235),
    ):
        c = QColor(colour)
        c.setAlpha(alpha)
        gradient.setColorAt(stop, c)
    return QBrush(gradient)


def paint_panel(
    painter: QPainter,
    panel: Panel,
    *,
    rim_angle: float | None = None,
    policy: motion.MotionPolicy | None = None,
) -> None:
    """Draw one glass panel. ``rim_angle`` is None for a static rim position."""
    policy = policy or motion.current()
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    rect = panel.rect.adjusted(0.5, 0.5, -0.5, -0.5)
    shape = QPainterPath()
    shape.addRoundedRect(rect, panel.radius, panel.radius)
    if policy.high_contrast:
        # Use the system's high-contrast colours so text from the OS palette stays readable.
        system = QApplication.palette()
        painter.setPen(QPen(system.color(QPalette.ColorRole.WindowText), 2))
        painter.setBrush(system.color(QPalette.ColorRole.Window))
        painter.drawPath(shape)
        painter.restore()
        return
    # Soft shadow only outside the panel, so the translucent fill stays clean.
    outside = QPainterPath()
    outside.addRect(rect.adjusted(-10, -8, 10, 14))
    outside.addPath(shape)
    painter.save()
    painter.setClipPath(outside)
    painter.setPen(Qt.PenStyle.NoPen)
    for grow, alpha in ((1, 12), (3, 8), (6, 4)):
        painter.setBrush(_shadow(alpha))
        painter.drawRoundedRect(
            rect.adjusted(-grow, -grow + 3, grow, grow + 3),
            panel.radius + grow,
            panel.radius + grow,
        )
    painter.restore()
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(surface_color(panel.role, policy))
    painter.drawPath(shape)
    sheen = QLinearGradient(rect.topLeft(), QPointF(rect.left(), rect.top() + rect.height() * 0.45))
    edge = QColor(tokens.EDGE)
    edge.setAlpha(tokens.SHEEN)
    sheen.setColorAt(0, edge)
    edge.setAlpha(0)
    sheen.setColorAt(1, edge)
    painter.setBrush(sheen)
    painter.drawPath(shape)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    if panel.rim:
        angle = STATIC_RIM_ANGLE if rim_angle is None else rim_angle
        painter.setPen(QPen(_rim_brush(rect, angle), 1.8))
    else:
        painter.setPen(QPen(_edge(235), 1.2))
    painter.drawPath(shape)
    painter.restore()


def _build_mesh(width: int, height: int, margin: int, ratio: float) -> QPixmap:
    full_w, full_h = width + 2 * margin, height + 2 * margin
    pixmap = QPixmap(int(full_w * ratio), int(full_h * ratio))
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(QColor(tokens.BASE))
    painter = QPainter(pixmap)
    reach = 0.62 * max(full_w, full_h)
    for (cx, cy), colour in zip(
        ((0.12, 0.08), (0.88, 0.16), (0.82, 0.94), (0.08, 0.88)), tokens.MESH, strict=True
    ):
        centre = QPointF(full_w * cx, full_h * cy)
        gradient = QRadialGradient(centre, reach)
        solid = QColor(colour)
        faded = QColor(colour)
        faded.setAlpha(0)
        gradient.setColorAt(0, solid)
        gradient.setColorAt(1, faded)
        painter.fillRect(QRectF(0, 0, full_w, full_h), gradient)
    painter.end()
    return pixmap


class GlassBackdrop(QObject):
    """Paints a window's mesh and panels, and owns its (optional) animation timer."""

    def __init__(
        self,
        widget: QWidget,
        panels: Callable[[], list[Panel]],
        *,
        animated: bool,
        radius: float = tokens.RADIUS_PANEL,
    ) -> None:
        super().__init__(widget)
        self._widget = widget
        self._panels = panels
        self._animated = animated
        self._radius = radius
        self._elapsed = 0.0
        self._started_at = 0.0
        self._mesh: QPixmap | None = None
        self._mesh_key: tuple[int, int, float] | None = None
        self._timer = QTimer(self)
        self._timer.setInterval(TICK_MS)
        self._timer.timeout.connect(self._tick)
        widget.installEventFilter(self)

    @property
    def running(self) -> bool:
        return self._timer.isActive()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if watched is self._widget and event.type() in (
            QEvent.Type.Show,
            QEvent.Type.Hide,
            QEvent.Type.Close,
            QEvent.Type.WindowStateChange,
            QEvent.Type.WindowActivate,
            QEvent.Type.WindowDeactivate,
            QEvent.Type.ThemeChange,
            QEvent.Type.PaletteChange,
            QEvent.Type.ApplicationPaletteChange,
        ):
            if event.type() not in (QEvent.Type.Hide, QEvent.Type.Close):
                motion.refresh()  # re-read OS motion/transparency preferences
            self.sync()
            if event.type() == QEvent.Type.ThemeChange:
                self._widget.update()
        return False

    def _may_animate(self) -> bool:
        window = self._widget.window()
        return (
            self._animated
            and self._widget.isVisible()
            and not window.isMinimized()
            and window.isActiveWindow()
            and motion.current().animations_allowed
        )

    def sync(self) -> None:
        """Start or stop the timer to match visibility, activation and the motion policy."""
        if self._may_animate():
            if not self._timer.isActive():
                self._started_at = time.monotonic() - self._elapsed
                self._timer.start()
        elif self._timer.isActive():
            self._timer.stop()

    def stop(self) -> None:
        self._timer.stop()

    def _tick(self) -> None:
        self.sync()
        if self._timer.isActive():
            self._elapsed = time.monotonic() - self._started_at
            self._widget.update()

    def advance(self, seconds: float) -> None:
        """Move the animation clock (tests and deterministic screenshots)."""
        self._elapsed += seconds
        self._widget.update()

    def _motion_time(self, policy: motion.MotionPolicy) -> float:
        return self._elapsed if self._animated and policy.animations_allowed else 0.0

    def paint(self, painter: QPainter) -> None:
        policy = motion.current()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self._widget.rect())
        outline = rect.adjusted(0.5, 0.5, -0.5, -0.5)
        window_shape = QPainterPath()
        window_shape.addRoundedRect(outline, self._radius, self._radius)
        painter.save()
        painter.setClipPath(window_shape)
        if policy.use_mesh:
            self._paint_mesh(painter, rect, policy)
        else:
            painter.fillRect(rect, self._widget.palette().window())
        painter.restore()
        angle: float | None = None
        seconds = self._motion_time(policy)
        if self._animated and policy.animations_allowed and seconds:
            angle = -360.0 * (seconds % tokens.RIM_PERIOD_SECONDS) / tokens.RIM_PERIOD_SECONDS
        for panel in self._panels():
            paint_panel(painter, panel, rim_angle=angle, policy=policy)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(_edge(220), 1.2))
        painter.drawPath(window_shape)

    def _paint_mesh(self, painter: QPainter, rect: QRectF, policy: motion.MotionPolicy) -> None:
        ratio = painter.device().devicePixelRatioF()
        width, height = max(1, int(rect.width())), max(1, int(rect.height()))
        margin = max(24, min(90, int(min(width, height) * 0.1)))
        key = (width, height, ratio)
        if self._mesh is None or self._mesh_key != key:
            self._mesh = _build_mesh(width, height, margin, ratio)
            self._mesh_key = key
        seconds = self._motion_time(policy)
        dx = dy = 0.0
        if seconds and policy.mesh_drift:
            cycle = (seconds % tokens.MESH_PERIOD_SECONDS) / tokens.MESH_PERIOD_SECONDS
            phase = 2 * math.pi * cycle
            dx = margin * 0.85 * math.sin(phase)
            dy = margin * 0.85 * math.sin(2 * phase + 1.0)
        painter.drawPixmap(QPointF(-margin + dx, -margin + dy), self._mesh)
