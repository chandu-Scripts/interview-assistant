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
from shiboken6 import isValid

from interview_assistant.config.keys import KeyStatus, KeyStore
from interview_assistant.config.provider_check import CheckResult, check_key
from interview_assistant.config.user_settings import UserSettingsStore
from interview_assistant.ui.bg import Runner, run_in_thread

Checker = Callable[[str, str], CheckResult]
NAMES = {
    "openai": "OpenAI",
    "anthropic": "Anthropic (Claude)",
    "gemini": "Google Gemini",
    "speechmatics": "Speechmatics",
}
HELP = {
    "openai": "platform.openai.com/api-keys",
    "anthropic": "console.anthropic.com/settings/keys",
    "gemini": "aistudio.google.com/apikey",
    "speechmatics": "portal.speechmatics.com",
}


class KeyRow(QWidget):
    """One provider's API key: status, paste box, Test / Save / Remove.

    Keys go to the credential store only; the field is masked and cleared after saving."""

    changed = Signal()  # the stored key was added/removed
    models_found = Signal(str, list)  # provider, model ids from a successful test
    tested = Signal(str, str, object)  # provider, credential fingerprint, CheckResult

    def __init__(
        self,
        provider: str,
        store: KeyStore,
        status: Callable[[], KeyStatus],
        checker: Checker = check_key,
        runner: Runner = run_in_thread,
        key_getter: Callable[[], str] | None = None,
        preferences: UserSettingsStore | None = None,
    ) -> None:
        super().__init__()
        self.provider = provider
        self._store = store
        self._status = status
        self._checker = checker
        self._runner = runner
        self._preferences = preferences
        self._key_getter = key_getter or (lambda: self._store.get(self.provider) or "")
        self._revision = 0
        self._busy = False
        self._last_test: tuple[str, CheckResult] | None = None
        self.title = QLabel(
            f"<b>{NAMES[provider]}</b>  <small>(get a key at {HELP[provider]})</small>"
        )
        self.state = QLabel("")
        self.state.setWordWrap(True)
        self.edit = QLineEdit()
        self.edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.edit.setPlaceholderText("Paste API key here")
        self.test_btn = QPushButton("Test key")
        self.test_btn.setToolTip(
            "Free authentication check. Does not check credits or generate an answer."
        )
        self.test_btn.setVisible(provider != "speechmatics")
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
        self.edit.textChanged.connect(self._edited)
        self.refresh()

    def refresh(self) -> None:
        st = self._status()
        hint = ""
        if st.source == "env" and self._preferences is not None:
            hint = "\nPaste a replacement below. Save makes the app use it instead after restart."
        self.state.setText(st.describe() + hint)
        self.edit.setEnabled(st.editable or self._preferences is not None)
        self.edit.setToolTip(
            hint.strip()
            if self.edit.isEnabled()
            else "Set by the environment / .env; edit it there."
        )
        self._update_buttons()

    def _update_buttons(self) -> None:
        st = self._status()
        typed = bool(self.edit.text().strip())
        self.test_btn.setEnabled((typed or st.present) and not self._busy)
        self.save_btn.setEnabled(typed and (st.editable or self._preferences is not None))
        self.remove_btn.setEnabled(st.source == "keyring")

    def _key_to_test(self) -> str:
        typed = self.edit.text().strip() if self.edit.isEnabled() else ""
        if typed:
            return typed
        return self._key_getter()

    def _edited(self) -> None:
        self._revision += 1
        self._last_test = None
        self.result.clear()
        self._update_buttons()

    def test(self) -> None:
        if self._busy:
            return
        if self.provider == "speechmatics":
            self.result.setText("Live Speechmatics connection testing is not enabled yet.")
            return
        key = self._key_to_test()
        if not key:
            self.result.setText("Nothing to test - paste a key first.")
            return
        self.result.setText("Testing…")
        self._busy = True
        self.test_btn.setEnabled(False)
        revision = self._revision

        def done(res: object) -> None:
            if not isValid(self):
                return
            self._busy = False
            self._update_buttons()
            # A response for an old draft/deleted/environment-overridden key is never green.
            if revision != self._revision or key != self._key_to_test():
                return
            if isinstance(res, CheckResult):
                from interview_assistant.ui.provider_catalog import fingerprint

                self._last_test = (fingerprint(key), res)
                self.result.setText(("✔ " if res.ok else "✖ ") + res.message)
                self.tested.emit(self.provider, fingerprint(key), res)
                if res.ok and res.models:
                    self.models_found.emit(self.provider, res.models)
            else:  # the check itself crashed
                self.result.setText(f"✖ Could not test the key ({type(res).__name__}).")
                from interview_assistant.ui.provider_catalog import fingerprint

                self.tested.emit(
                    self.provider, fingerprint(key), CheckResult(False, "Could not test the key.")
                )

        self._runner(lambda: self._checker(self.provider, key), done)

    def save(self) -> None:
        if not self._status().editable and self._preferences is None:
            return
        key = self.edit.text().strip()
        if not key:
            return
        try:
            previous = self._store.get(self.provider)
            self._store.set(self.provider, key)
            if self._preferences is not None:
                try:
                    preferred = list(self._preferences.load().get("prefer_saved_keys", []))
                    if self.provider not in preferred:
                        preferred.append(self.provider)
                        self._preferences.update({"prefer_saved_keys": preferred})
                except Exception:
                    # Do not leave a replacement active if selecting its source failed.
                    if previous is None:
                        self._store.delete(self.provider)
                    else:
                        self._store.set(self.provider, previous)
                    raise
        except Exception as e:
            self.result.setText(
                f"✖ Could not save the key and its source preference ({type(e).__name__})."
            )
            return
        last_test = self._last_test
        self.edit.clear()
        self.result.setText("✔ Saved to Windows Credential Manager. Restart to use this key.")
        self.refresh()
        self.changed.emit()
        if last_test is not None:
            self.tested.emit(self.provider, *last_test)

    def remove(self) -> None:
        if self._status().source != "keyring":
            return
        try:
            self._store.delete(self.provider)
            if self._store.get(self.provider):
                self.result.setText(
                    "The key is still in Windows Credential Manager. Try removing it again."
                )
                return
        except Exception:
            self.result.setText("Could not remove the key from Windows Credential Manager.")
            return
        self._revision += 1
        self._last_test = None
        self.edit.clear()
        self.result.setText("Key removed.")
        if self._preferences is not None:
            self.result.setText("Key removed. Paste and save a new key to enable this provider.")
        self.refresh()
        self.changed.emit()
