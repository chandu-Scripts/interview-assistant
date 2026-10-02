from collections.abc import Callable

from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from interview_assistant.app.presentation import PresentationMode
from interview_assistant.privacy.disclosure import DataFlow
from interview_assistant.privacy.state import PrivacyState


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
    ) -> None:
        super().__init__()
        self._state = state
        self._presentation = presentation
        self._flows = flows
        self._confirm = confirm
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
        layout.addWidget(QLabel("Where your data goes:"))
        layout.addWidget(self.flow_view, 1)

        self.incognito_box.toggled.connect(self._state.set_incognito)
        self.redact_box.toggled.connect(self._state.set_redact)
        self.presentation_btn.clicked.connect(self._presentation.toggle)
        self.wipe_btn.clicked.connect(self.wipe)
        self._state.changed.connect(self.refresh)
        self._presentation.changed.connect(lambda _on: self.refresh())
        self.refresh()

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
