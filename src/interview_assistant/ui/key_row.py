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

from interview_assistant.config.keys import KeyStatus, KeyStore
from interview_assistant.config.provider_check import CheckResult, check_key
from interview_assistant.ui.bg import Runner, run_in_thread

Checker = Callable[[str, str], CheckResult]
NAMES = {"openai": "OpenAI", "anthropic": "Anthropic (Claude)", "gemini": "Google Gemini"}
HELP = {
    "openai": "platform.openai.com/api-keys",
    "anthropic": "console.anthropic.com/settings/keys",
    "gemini": "aistudio.google.com/apikey",
}


class KeyRow(QWidget):
    """One provider's API key: status, paste box, Test / Save / Remove.

    Keys go to the credential store only; the field is masked and cleared after saving."""

    changed = Signal()  # the stored key was added/removed
    models_found = Signal(str, list)  # provider, model ids from a successful test

    def __init__(
        self,
        provider: str,
        store: KeyStore,
        status: Callable[[], KeyStatus],
        checker: Checker = check_key,
        runner: Runner = run_in_thread,
    ) -> None:
        super().__init__()
        self.provider = provider
        self._store = store
        self._status = status
        self._checker = checker
        self._runner = runner
        self.title = QLabel(
            f"<b>{NAMES[provider]}</b>  <small>(get a key at {HELP[provider]})</small>"
        )
        self.state = QLabel("")
        self.edit = QLineEdit()
        self.edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.edit.setPlaceholderText("Paste API key here")
        self.test_btn = QPushButton("Test")
        self.save_btn = QPushButton("Save")
        self.remove_btn = QPushButton("Remove")
        self.result = QLabel("")
        self.result.setWordWrap(True)
        row = QHBoxLayout()
        row.addWidget(self.edit, 1)
        for b in (self.test_btn, self.save_btn, self.remove_btn):
            row.addWidget(b)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.addWidget(self.title)
        layout.addWidget(self.state)
        layout.addLayout(row)
        layout.addWidget(self.result)
        self.test_btn.clicked.connect(self.test)
        self.save_btn.clicked.connect(self.save)
        self.remove_btn.clicked.connect(self.remove)
        self.edit.textChanged.connect(lambda _t: self._update_buttons())
        self.refresh()

    def refresh(self) -> None:
        st = self._status()
        self.state.setText(st.describe())
        self.edit.setEnabled(st.editable)
        self.edit.setToolTip("" if st.editable else "Set by the environment / .env; edit it there.")
        self._update_buttons()

    def _update_buttons(self) -> None:
        st = self._status()
        typed = bool(self.edit.text().strip())
        self.test_btn.setEnabled(typed or st.present)
        self.save_btn.setEnabled(typed and st.editable)
        self.remove_btn.setEnabled(st.source == "keyring")

    def _key_to_test(self) -> str:
        typed = self.edit.text().strip()
        if typed:
            return typed
        return self._store.get(self.provider) or ""

    def test(self) -> None:
        key = self._key_to_test()
        if not key:
            self.result.setText("Nothing to test - paste a key first.")
            return
        self.result.setText("Testing…")
        self.test_btn.setEnabled(False)

        def done(res: object) -> None:
            self._update_buttons()
            if isinstance(res, CheckResult):
                self.result.setText(("✔ " if res.ok else "✖ ") + res.message)
                if res.ok and res.models:
                    self.models_found.emit(self.provider, res.models)
            else:  # the check itself crashed
                self.result.setText(f"✖ Could not test the key ({type(res).__name__}).")

        self._runner(lambda: self._checker(self.provider, key), done)

    def save(self) -> None:
        key = self.edit.text().strip()
        if not key:
            return
        try:
            self._store.set(self.provider, key)
        except Exception as e:
            self.result.setText(f"✖ Could not save to the credential store ({type(e).__name__}).")
            return
        self.edit.clear()
        self.result.setText("✔ Saved to Windows Credential Manager.")
        self.refresh()
        self.changed.emit()

    def remove(self) -> None:
        self._store.delete(self.provider)
        self.result.setText("Key removed.")
        self.refresh()
        self.changed.emit()
