from collections.abc import Callable

from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from interview_assistant.app.presentation import PresentationMode
from interview_assistant.privacy.capture_visibility import CapturePrivacy
from interview_assistant.privacy.disclosure import DataFlow
from interview_assistant.privacy.state import PrivacyState


class CapturePrivacyIndicator(QLabel):
    """Compact local status; does not break the overlay's existing bubble mode."""

    def __init__(self, manager: CapturePrivacy, overlay: QWidget, parent: QWidget) -> None:
        super().__init__(parent)
        self._manager = manager
        self._overlay = overlay
        self.setObjectName("capturePrivacyStatus")
        self.setAccessibleName("Screen-share privacy status")
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(54)
        self.setMaximumWidth(80)
        self.setWordWrap(True)
        overlay.installEventFilter(self)
        manager.changed.connect(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        manager = self._manager
        self.setText("Privacy On" if manager.enabled else "Unavailable")
        self.setToolTip(
            f"Screen-share privacy: {manager.status}. Supported Windows capture only; "
            "verify on the receiver's screen. Native popups may briefly appear."
        )
        self.setVisible(manager.status != "Off" and not getattr(self._overlay, "_bubble", False))

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if watched is self._overlay and event.type() in (
            QEvent.Type.Resize, QEvent.Type.LayoutRequest, QEvent.Type.Show
        ):
            self.refresh()
        return False


def _default_confirm(text: str) -> bool:
    r = QMessageBox.question(
        None, "Confirm", text, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
    )
    return r == QMessageBox.StandardButton.Yes


class PrivacyDialog(QWidget):
    """One place to see and control what is stored, what is sent, and to enter presentation mode."""

    def __init__(
        self,
        state: PrivacyState,
        presentation: PresentationMode,
        flows: Callable[[], list[DataFlow]],
        hotkey_hint: str = "",
        confirm: Callable[[str], bool] = _default_confirm,
        capture_privacy: CapturePrivacy | None = None,
    ) -> None:
        super().__init__()
        self._state = state
        self._presentation = presentation
        self._flows = flows
        self._confirm = confirm
        self._capture_privacy = capture_privacy
        self.setWindowTitle("Privacy")
        self.resize(560, 460)

        self.incognito_box = QCheckBox("Incognito - save nothing (no history, no usage log)")
        self.redact_box = QCheckBox(
            "Mask emails, phone numbers, card numbers and API keys before sending to AI"
        )
        self.presentation_btn = QPushButton()
        self.wipe_btn = QPushButton("Delete all saved data…")
        self.flow_view = QTextBrowser()
        note = QLabel(
            "Presentation mode hides the assistant on your own screen and pauses listening and "
            "answering. It is only ever turned on by you and is off every time the app starts."
            + (f"  Hotkey: {hotkey_hint}" if hotkey_hint else "")
        )
        note.setWordWrap(True)

        row = QHBoxLayout()
        row.addWidget(self.presentation_btn)
        row.addStretch(1)
        row.addWidget(self.wipe_btn)
        layout = QVBoxLayout(self)
        layout.addWidget(self.incognito_box)
        layout.addWidget(self.redact_box)
        layout.addLayout(row)
        layout.addWidget(note)
        self.capture_box = QCheckBox("Screen-share privacy (supported Windows capture only)")
        self.capture_status = QLabel()
        self.capture_status.setWordWrap(True)
        self.capture_reset = QPushButton("Retry turning screen-share privacy off")
        capture_note = QLabel(
            "Keeps this app visible on your screen and asks supported Windows capture tools "
            "to exclude its windows. Listening and answers continue. Off at every startup. "
            "Some sharing tools can still capture it; native popups may briefly appear. "
            "Verify from the receiver's screen before relying on it."
        )
        capture_note.setWordWrap(True)
        for control in (self.capture_box, self.capture_status, self.capture_reset, capture_note):
            layout.addWidget(control)
            control.setVisible(capture_privacy is not None)
        if capture_privacy is not None:
            self.capture_box.toggled.connect(capture_privacy.set_enabled)
            self.capture_reset.clicked.connect(lambda: capture_privacy.set_enabled(False))
            capture_privacy.changed.connect(self.refresh_capture)
            self.refresh_capture()
        layout.addWidget(QLabel("Where your data goes:"))
        layout.addWidget(self.flow_view, 1)

        self.incognito_box.toggled.connect(self._state.set_incognito)
        self.redact_box.toggled.connect(self._state.set_redact)
        self.presentation_btn.clicked.connect(self._presentation.toggle)
        self.wipe_btn.clicked.connect(self.wipe)
        self._state.changed.connect(self.refresh)
        self._presentation.changed.connect(lambda _on: self.refresh())
        self.refresh()

    def refresh_capture(self) -> None:
        if self._capture_privacy is None:
            return
        manager = self._capture_privacy
        self.capture_box.blockSignals(True)
        self.capture_box.setChecked(manager.enabled)
        self.capture_box.blockSignals(False)
        self.capture_status.setText(f"Screen-share privacy: {manager.status}")
        self.capture_reset.setVisible("reset failed" in manager.status)

    def refresh(self) -> None:
        for box, value in (
            (self.incognito_box, self._state.incognito),
            (self.redact_box, self._state.redact),
        ):
            box.blockSignals(True)
            box.setChecked(value)
            box.blockSignals(False)
        self.presentation_btn.setText(
            "End presentation mode" if self._presentation.active else "Start presentation mode"
        )
        self.flow_view.setPlainText("\n".join(f.line() for f in self._flows()))

    def wipe(self) -> None:
        if self._confirm(
            "Permanently delete all saved questions, answers and usage data on this PC? "
            "This cannot be undone."
        ):
            self._state.wipe_all()
