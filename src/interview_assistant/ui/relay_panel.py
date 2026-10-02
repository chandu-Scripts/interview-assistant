"""UI for the "Shared access" flow: request access to someone else's relay, poll for approval,
store the issued token. Kept separate from key_row.py's provider-key UI - a relay token is a
different kind of credential (issued by someone else's one-click approval, not "your own AI
provider key from platform.openai.com") - so it gets its own small panel, not a 4th KeyRow.

Like KeyRow, relay_url/relay_pending_id/the token are written immediately when an action
completes (not deferred to the dialog's Save button): this is operational state ("where do we
stand with this request"), not a preference you'd want a Cancel click to discard.
"""

from collections.abc import Callable

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from interview_assistant.config.relay import (
    RelayTokenStore,
    RequestResult,
    StatusResult,
    check_status,
    request_access,
)
from interview_assistant.config.settings import Settings
from interview_assistant.config.user_settings import UserSettingsStore
from interview_assistant.ui.bg import Runner, run_in_thread

RequestFn = Callable[[str, str], RequestResult]
StatusFn = Callable[[str, str], StatusResult]


class RelayPanel(QWidget):
    """Connect to someone else's relay under their own AI billing, once they approve you."""

    connected = Signal()  # a token was obtained or removed - caller may flag "restart needed"

    def __init__(
        self,
        user_settings: UserSettingsStore,
        token_store: RelayTokenStore,
        raw_settings: Callable[[], Settings] = Settings,
        request_fn: RequestFn = request_access,
        status_fn: StatusFn = check_status,
        runner: Runner = run_in_thread,
    ) -> None:
        super().__init__()
        self._store = user_settings
        self._tokens = token_store
        self._raw = raw_settings
        self._request_fn = request_fn
        self._status_fn = status_fn
        self._runner = runner

        self.intro = QLabel(
            "Ask someone to share their AI access with you: enter the address they gave you and "
            "your name, then request access. They approve it from their own admin page; once "
            "approved, this app uses their access (within the budget they set) instead of "
            "needing your own API keys."
        )
        self.intro.setWordWrap(True)
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("https://their-relay-address")
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Your name (shown to them for approval)")
        self.request_btn = QPushButton("Request access")
        self.check_btn = QPushButton("Check now")
        self.disconnect_btn = QPushButton("Disconnect")
        self.status = QLabel("")
        self.status.setWordWrap(True)

        url_row = QHBoxLayout()
        url_row.addWidget(QLabel("Relay address"))
        url_row.addWidget(self.url_edit, 1)
        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("Your name"))
        name_row.addWidget(self.name_edit, 1)
        buttons = QHBoxLayout()
        buttons.addWidget(self.request_btn)
        buttons.addWidget(self.check_btn)
        buttons.addWidget(self.disconnect_btn)
        layout = QVBoxLayout(self)
        layout.addWidget(self.intro)
        layout.addLayout(url_row)
        layout.addLayout(name_row)
        layout.addLayout(buttons)
        layout.addWidget(self.status)
        layout.addStretch(1)

        self.request_btn.clicked.connect(self.request)
        self.check_btn.clicked.connect(self.check)
        self.disconnect_btn.clicked.connect(self.forget_access)
        self.refresh()

    # ---- state ----
    def refresh(self) -> None:
        s = self._raw()
        self.url_edit.setText(s.relay_url or "")
        connected = bool(self._tokens.get())
        pending = bool(s.relay_pending_id)
        if connected:
            self.status.setText("✔ Connected. Using their shared access.")
        elif pending:
            self.status.setText('Request sent - waiting for them to approve. Click "Check now".')
        else:
            self.status.setText("Not connected.")
        self.request_btn.setEnabled(not connected and not pending)
        self.check_btn.setEnabled(pending and not connected)
        self.disconnect_btn.setEnabled(connected or pending)
        self.url_edit.setEnabled(not connected and not pending)

    # ---- actions ----
    def request(self) -> None:
        url = self.url_edit.text().strip()
        name = self.name_edit.text().strip()
        if not url or not name:
            self.status.setText("Enter both the relay address and your name.")
            return
        self.request_btn.setEnabled(False)
        self.status.setText("Requesting…")

        def done(res: object) -> None:
            if isinstance(res, RequestResult):
                self._store.update({"relay_url": url, "relay_pending_id": res.public_id})
                self.refresh()
                self.status.setText('Request sent. Ask them to approve it, then click "Check now".')
            else:
                self.request_btn.setEnabled(True)
                self.status.setText(f"✖ Could not send the request ({_err(res)}).")

        self._runner(lambda: self._request_fn(url, name), done)

    def check(self) -> None:
        s = self._raw()
        url, pending = s.relay_url, s.relay_pending_id
        if not url or not pending:
            return
        self.check_btn.setEnabled(False)
        self.status.setText("Checking…")

        def done(res: object) -> None:
            self.check_btn.setEnabled(True)
            if not isinstance(res, StatusResult):
                self.status.setText(f"✖ Could not check status ({_err(res)}).")
                return
            if res.status == "pending":
                self.status.setText("Still waiting for approval.")
            elif res.status == "denied":
                self._store.update({"relay_pending_id": None})
                self.refresh()
                self.status.setText("The request was denied.")
            elif res.status == "approved":
                if res.token:
                    self._tokens.set(res.token)
                self._store.update({"relay_pending_id": None})
                self.refresh()
                self.status.setText("✔ Connected. Restart the app for it to take effect.")
                self.connected.emit()
            else:
                self.status.setText(f"Unexpected status from the relay: {res.status!r}")

        self._runner(lambda: self._status_fn(url, pending), done)

    def forget_access(self) -> None:
        self._tokens.delete()
        self._store.update({"relay_pending_id": None})
        self.refresh()
        self.status.setText("Disconnected.")
        self.connected.emit()


def _err(res: object) -> str:
    return type(res).__name__
