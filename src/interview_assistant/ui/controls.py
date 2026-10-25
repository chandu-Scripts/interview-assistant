"""Shared input behavior for scrollable app screens."""

from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QObject,
    QPoint,
    QPointF,
    QRectF,
    QSize,
    Qt,
    QVariantAnimation,
)
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPaintEvent, QPen, QWheelEvent
from PySide6.QtWidgets import QAbstractScrollArea, QApplication, QCheckBox, QComboBox, QWidget


class ToggleSwitch(QCheckBox):
    """Accessible checkable switch with an animated thumb and glass-theme colours."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._position = 0.0
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._animation = QVariantAnimation(self)
        self._animation.setDuration(160)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._animation.valueChanged.connect(self._move_thumb)
        self.toggled.connect(self._animate)

    def _move_thumb(self, value: float) -> None:
        self._position = float(value)
        self.update()

    def _animate(self, checked: bool) -> None:
        self._animation.stop()
        if not self.isVisible():
            self._move_thumb(float(checked))
            return
        self._animation.setStartValue(self._position)
        self._animation.setEndValue(float(checked))
        self._animation.start()

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(68 + self.fontMetrics().horizontalAdvance(self.text()), 36)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return self.sizeHint()

    def hitButton(self, position: QPoint) -> bool:  # noqa: N802
        return self.rect().contains(position)

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self.isEnabled():
            painter.setOpacity(0.45)
        track = QRectF(3, (self.height() - 26) / 2, 48, 26)
        gradient = QLinearGradient(track.topLeft(), track.bottomRight())
        gradient.setColorAt(0, QColor("#77e5df" if self.isChecked() else "#334b66"))
        gradient.setColorAt(1, QColor("#328d9b" if self.isChecked() else "#172c42"))
        painter.setPen(QPen(QColor("#8af0ed" if self.isChecked() else "#60849d"), 1))
        painter.setBrush(gradient)
        painter.drawRoundedRect(track, 13, 13)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#efffff"))
        painter.drawEllipse(QRectF(6 + 22 * self._position, track.top() + 3, 20, 20))
        painter.setPen(QColor("#8af0ed" if self.isChecked() else "#a7b9d2"))
        painter.drawText(
            self.rect().adjusted(64, 0, -2, 0),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            self.text(),
        )
        if self.hasFocus():
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor("#77e5df"), 1, Qt.PenStyle.DashLine))
            painter.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 6, 6)


class ChoiceComboBox(QComboBox):
    """Choose explicitly; scrolling over the field must never change its value."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def setEditable(self, editable: bool) -> None:  # noqa: N802
        super().setEditable(editable)
        editor = self.lineEdit()
        if editor is not None:
            editor.installEventFilter(self)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if isinstance(event, QWheelEvent) and watched is self.lineEdit():
            self.wheelEvent(event)
            return True
        return super().eventFilter(watched, event)

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        # Forward to the enclosing viewport: ignoring alone can swallow wheel input
        # on Windows. Popup item views handle their own scrolling and click selection.
        parent = self.parentWidget()
        while parent is not None:
            if isinstance(parent, QAbstractScrollArea):
                viewport = parent.viewport()
                forwarded = QWheelEvent(
                    QPointF(viewport.mapFromGlobal(event.globalPosition().toPoint())),
                    event.globalPosition(),
                    event.pixelDelta(),
                    event.angleDelta(),
                    event.buttons(),
                    event.modifiers(),
                    event.phase(),
                    event.inverted(),
                    event.source(),
                    event.pointingDevice(),
                )
                QApplication.sendEvent(viewport, forwarded)
                event.accept()
                return
            parent = parent.parentWidget()
        event.ignore()
