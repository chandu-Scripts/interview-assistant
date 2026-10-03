from collections.abc import Callable
from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from interview_assistant.app.hotkeys import parse_hotkey
from interview_assistant.config.keys import PROVIDERS, KeyStatus, KeyStore, key_status
from interview_assistant.config.provider_check import check_key
from interview_assistant.config.relay import (
    KeyringRelayTokenStore,
    RelayTokenStore,
    check_status,
    request_access,
)
from interview_assistant.config.settings import Settings
from interview_assistant.config.user_settings import UserSettingsStore
from interview_assistant.llm.router_factory import parse_chain
from interview_assistant.ui.bg import Runner, run_in_thread
from interview_assistant.ui.key_row import Checker, KeyRow
from interview_assistant.ui.relay_panel import RelayPanel, RequestFn, StatusFn

WHISPER_CHOICES = [
    ("tiny.en - fastest, least accurate", "tiny.en"),
    ("base.en - balanced (default)", "base.en"),
    ("small.en - more accurate, ~3x slower", "small.en"),
    ("medium.en - most accurate, slow on CPU", "medium.en"),
]
MODEL_FIELDS = [
    ("openai_model", "OpenAI - fast"),
    ("openai_smart_model", "OpenAI - smart"),
    ("anthropic_model", "Anthropic - smart"),
    ("anthropic_fast_model", "Anthropic - fast"),
    ("gemini_model", "Gemini - smart"),
    ("gemini_fast_model", "Gemini - fast"),
]
MODEL_PROVIDER = {f: f.split("_")[0] for f, _ in MODEL_FIELDS}


class SettingsDialog(QDialog):
    """Edit keys and settings. Keys are stored immediately in the credential store; the other
    settings are saved to settings.json. Most changes apply after a restart."""

    saved = Signal(bool)  # needs_restart

    def __init__(
        self,
        store: UserSettingsStore,
        keys: KeyStore,
        raw_settings: Callable[[], Settings] = Settings,
        checker: Checker = check_key,
        runner: Runner = run_in_thread,
        relay_store: RelayTokenStore | None = None,
        relay_request_fn: RequestFn = request_access,
        relay_status_fn: StatusFn = check_status,
    ) -> None:
        super().__init__()
        self._store = store
        self._keys = keys
        self._raw = raw_settings
        self._keys_changed = False
        self._initial: dict[str, Any] = {}
        self.setWindowTitle("Settings")
        self.resize(640, 560)

        self.tabs = QTabWidget()
        self.key_rows: dict[str, KeyRow] = {}
        self.tabs.addTab(self._providers_tab(checker, runner), "AI providers")
        self.tabs.addTab(self._speech_tab(), "Speech")
        self.tabs.addTab(self._profile_tab(), "Profile")
        self.tabs.addTab(self._budget_tab(), "Budget && history")
        self.tabs.addTab(self._privacy_tab(), "Privacy && hotkeys")
        self.tabs.addTab(
            self._shared_access_tab(
                relay_store or KeyringRelayTokenStore(), relay_request_fn, relay_status_fn, runner
            ),
            "Shared access",
        )
        self.message = QLabel("")
        self.message.setWordWrap(True)
        self.save_btn = QPushButton("Save")
        self.cancel_btn = QPushButton("Cancel")
        buttons = QHBoxLayout()
        buttons.addWidget(self.message, 1)
        buttons.addWidget(self.save_btn)
        buttons.addWidget(self.cancel_btn)
        layout = QVBoxLayout(self)
        layout.addWidget(self.tabs, 1)
        layout.addLayout(buttons)
        self.save_btn.clicked.connect(self.save)
        self.cancel_btn.clicked.connect(self.reject)
        self.load()

    # ---- tabs ----
    def _providers_tab(self, checker: Checker, runner: Runner) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        note = QLabel(
            "Keys are stored encrypted in the Windows Credential Manager, never in a file. "
            "A key set in the environment or .env overrides the stored one."
        )
        note.setWordWrap(True)
        lay.addWidget(note)
        for p in PROVIDERS:
            row = KeyRow(p, self._keys, self._status_fn(p), checker, runner)
            row.changed.connect(self._on_key_changed)
            row.models_found.connect(self._fill_models)
            self.key_rows[p] = row
            lay.addWidget(row)
        form = QFormLayout()
        self.model_boxes: dict[str, QComboBox] = {}
        for field, label in MODEL_FIELDS:
            box = QComboBox()
            box.setEditable(True)
            self.model_boxes[field] = box
            form.addRow(label, box)
        self.router_fast = QLineEdit()
        self.router_fast.setPlaceholderText(
            "auto (e.g. openai:gpt-4o-mini,gemini:gemini-flash-latest)"
        )
        self.router_smart = QLineEdit()
        self.router_smart.setPlaceholderText("auto")
        form.addRow("Fast chain (advanced)", self.router_fast)
        form.addRow("Smart chain (advanced)", self.router_smart)
        lay.addWidget(
            QLabel(
                "Models (a successful key test fills these lists with what your account offers):"
            )
        )
        lay.addLayout(form)
        lay.addStretch(1)
        return w

    def _speech_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        self.stt_provider = QComboBox()
        self.stt_provider.addItem("Local Whisper - offline, free, private", "local")
        self.stt_provider.addItem("OpenAI cloud - sends audio to OpenAI, billed", "openai")
        self.whisper_model = QComboBox()
        for label, value in WHISPER_CHOICES:
            self.whisper_model.addItem(label, value)
        self.stt_language = QLineEdit()
        self.stt_language.setPlaceholderText("en (leave empty to auto-detect)")
        self.stt_vocabulary = QLineEdit()
        self.stt_vocabulary.setPlaceholderText("terms the recognizer should expect (empty = off)")
        self.default_audio_kind = QComboBox()
        self.default_audio_kind.addItem("System audio (the interviewer's voice)", "loopback")
        self.default_audio_kind.addItem("Microphone", "mic")
        self.dual_audio = QCheckBox("Capture system audio AND microphone at the same time")
        self.dual_audio.setToolTip(
            "Both sources are transcribed simultaneously instead of picking one above. Doubles "
            "the local speech model's memory use (if using Local Whisper)."
        )
        form.addRow("Speech-to-text", self.stt_provider)
        form.addRow("Local model", self.whisper_model)
        form.addRow("Language", self.stt_language)
        form.addRow("Vocabulary hint", self.stt_vocabulary)
        form.addRow("Pre-selected audio source", self.default_audio_kind)
        form.addRow(self.dual_audio)
        return w

    def _profile_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(
            QLabel(
                "Your background, sent with each question so answers fit you. Emails and phone "
                "numbers are masked before sending if redaction is on."
            )
        )
        self.profile = QPlainTextEdit()
        self.profile.setPlaceholderText(
            "e.g. Backend engineer, 5 years Python/Go, payments platform…"
        )
        lay.addWidget(self.profile, 1)
        return w

    def _budget_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        self.budget_on = QCheckBox("Limit spending")
        self.budget_usd = QDoubleSpinBox()
        self.budget_usd.setRange(0.01, 10000)
        self.budget_usd.setDecimals(2)
        self.budget_usd.setPrefix("$")
        self.budget_scope = QComboBox()
        for label, value in (
            ("per session", "session"),
            ("per day", "day"),
            ("per month", "month"),
        ):
            self.budget_scope.addItem(label, value)
        self.budget_hard = QCheckBox("Stop answering when the limit is reached")
        self.auto_generate = QCheckBox("Auto Generate (Beta): answer voice questions immediately")
        self.auto_generate.setToolTip(
            "Off (default): a detected voice question waits for you to click Answer or press "
            "Ctrl+Enter. On: answered automatically, like before this setting existed. Typed "
            "questions and Chat always answer immediately either way."
        )
        self.history_on = QCheckBox("Save question/answer history on this PC")
        self.retention_on = QCheckBox("Auto-delete history older than")
        self.retention_days = QSpinBox()
        self.retention_days.setRange(1, 3650)
        self.retention_days.setSuffix(" days")
        form.addRow(self.budget_on, self.budget_usd)
        form.addRow("Budget period", self.budget_scope)
        form.addRow(self.budget_hard)
        form.addRow(self.auto_generate)
        form.addRow(self.history_on)
        form.addRow(self.retention_on, self.retention_days)
        return w

    def _privacy_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        self.incognito = QCheckBox("Start in incognito (save nothing)")
        self.redact = QCheckBox("Mask emails, phones, cards and API keys before sending to AI")
        self.consent = QCheckBox("Show the recording notice before the first capture")
        self.hotkey_presentation = QLineEdit()
        self.hotkey_overlay = QLineEdit()
        self.hotkey_bubble = QLineEdit()
        self.hotkey_answer = QLineEdit()
        for e in (
            self.hotkey_presentation,
            self.hotkey_overlay,
            self.hotkey_bubble,
            self.hotkey_answer,
        ):
            e.setPlaceholderText("e.g. ctrl+alt+p (empty = disabled)")
        form.addRow(self.incognito)
        form.addRow(self.redact)
        form.addRow(self.consent)
        form.addRow("Hotkey: presentation mode", self.hotkey_presentation)
        form.addRow("Hotkey: show/hide overlay", self.hotkey_overlay)
        form.addRow("Hotkey: collapse to bubble", self.hotkey_bubble)
        form.addRow("Hotkey: answer from any window", self.hotkey_answer)
        return w

    def _shared_access_tab(
        self,
        relay_store: RelayTokenStore,
        request_fn: RequestFn,
        status_fn: StatusFn,
        runner: Runner,
    ) -> QWidget:
        self.relay_panel = RelayPanel(
            self._store, relay_store, self._raw, request_fn, status_fn, runner
        )
        self.relay_panel.connected.connect(self._on_relay_connected)
        return self.relay_panel

    def _on_relay_connected(self) -> None:
        self._keys_changed = True  # reuses the existing "a credential changed" restart flag
        self.message.setText("Shared access updated. Restart the app for it to take effect.")

    def _status_fn(self, provider: str) -> Callable[[], KeyStatus]:
        return lambda: key_status(provider, self._raw(), self._keys)

    # ---- model lists ----
    def _fill_models(self, provider: str, models: list[str]) -> None:
        for field, box in self.model_boxes.items():
            if MODEL_PROVIDER[field] == provider:
                current = box.currentText()
                box.clear()
                box.addItems(models)
                box.setCurrentText(current)

    def _on_key_changed(self) -> None:
        self._keys_changed = True
        self.message.setText("Key changes are saved. Restart the app for them to take effect.")

    # ---- load / collect / save ----
    def load(self) -> None:
        s = self._raw()
        for field, box in self.model_boxes.items():
            box.setCurrentText(getattr(s, field))
        self.router_fast.setText(s.router_fast or "")
        self.router_smart.setText(s.router_smart or "")
        self._select(self.stt_provider, s.stt_provider)
        self._select_or_add(self.whisper_model, s.whisper_model)
        self.stt_language.setText(s.stt_language or "")
        self.stt_vocabulary.setText(s.stt_vocabulary or "")
        self._select(self.default_audio_kind, s.default_audio_kind)
        self.dual_audio.setChecked(s.dual_audio_capture)
        self.profile.setPlainText(s.candidate_profile or "")
        self.budget_on.setChecked(s.budget_usd is not None)
        self.budget_usd.setValue(s.budget_usd or 5.0)
        self._select(self.budget_scope, s.budget_scope)
        self.budget_hard.setChecked(s.budget_hard_stop)
        self.auto_generate.setChecked(s.answer_mode == "auto")
        self.history_on.setChecked(s.history_enabled)
        self.retention_on.setChecked(s.history_retention_days is not None)
        self.retention_days.setValue(s.history_retention_days or 30)
        self.incognito.setChecked(s.incognito)
        self.redact.setChecked(s.redact_pii)
        self.consent.setChecked(s.require_consent)
        self.hotkey_presentation.setText(s.hotkey_presentation)
        self.hotkey_overlay.setText(s.hotkey_overlay)
        self.hotkey_bubble.setText(s.hotkey_bubble)
        self.hotkey_answer.setText(s.hotkey_answer)
        for row in self.key_rows.values():
            row.refresh()
        self.relay_panel.refresh()
        self._initial = self.collect()

    @staticmethod
    def _select(combo: QComboBox, value: str) -> None:
        i = combo.findData(value)
        combo.setCurrentIndex(max(i, 0))

    @staticmethod
    def _select_or_add(combo: QComboBox, value: str) -> None:
        if combo.findData(value) < 0:
            combo.addItem(value, value)
        combo.setCurrentIndex(combo.findData(value))

    def collect(self) -> dict[str, Any]:
        """Form values as saved settings. Raises ValueError with a user-readable reason."""
        values: dict[str, Any] = {f: b.currentText().strip() for f, b in self.model_boxes.items()}
        for field, edit in (("router_fast", self.router_fast), ("router_smart", self.router_smart)):
            text = edit.text().strip()
            if text:
                try:
                    parse_chain(text)
                except ValueError as e:
                    raise ValueError(f"{field}: {e}") from e
            values[field] = text or None
        values["stt_provider"] = self.stt_provider.currentData()
        values["whisper_model"] = self.whisper_model.currentData()
        values["stt_language"] = self.stt_language.text().strip() or None
        values["stt_vocabulary"] = self.stt_vocabulary.text().strip()
        values["default_audio_kind"] = self.default_audio_kind.currentData()
        values["dual_audio_capture"] = self.dual_audio.isChecked()
        values["candidate_profile"] = self.profile.toPlainText().strip() or None
        values["budget_usd"] = self.budget_usd.value() if self.budget_on.isChecked() else None
        values["budget_scope"] = self.budget_scope.currentData()
        values["budget_hard_stop"] = self.budget_hard.isChecked()
        values["answer_mode"] = "auto" if self.auto_generate.isChecked() else "manual"
        values["history_enabled"] = self.history_on.isChecked()
        values["history_retention_days"] = (
            self.retention_days.value() if self.retention_on.isChecked() else None
        )
        values["incognito"] = self.incognito.isChecked()
        values["redact_pii"] = self.redact.isChecked()
        values["require_consent"] = self.consent.isChecked()
        for field, edit in (
            ("hotkey_presentation", self.hotkey_presentation),
            ("hotkey_overlay", self.hotkey_overlay),
            ("hotkey_bubble", self.hotkey_bubble),
            ("hotkey_answer", self.hotkey_answer),
        ):
            spec = edit.text().strip()
            if spec:
                try:
                    parse_hotkey(spec)
                except ValueError as e:
                    raise ValueError(str(e)) from e
            values[field] = spec
        if (
            values["stt_provider"] == "openai"
            and not key_status("openai", self._raw(), self._keys).present
        ):
            raise ValueError("Cloud speech-to-text needs an OpenAI key (AI providers tab).")
        return values

    def save(self) -> None:
        try:
            values = self.collect()
            changed = {k: v for k, v in values.items() if v != self._initial.get(k)}
            if changed:
                self._store.save(self._to_store(values))
        except ValueError as e:
            self.message.setText(f"Not saved: {e}")
            return
        needs_restart = bool(changed) or self._keys_changed
        self._initial = values
        self.message.setText("Saved." + (" Restart to apply." if needs_restart else ""))
        self.saved.emit(needs_restart)
        self.accept()

    def _to_store(self, values: dict[str, Any]) -> dict[str, Any]:
        """Merge the form into the saved file, keeping only values that differ from the built-in
        defaults - so a future app update can still improve defaults (e.g. renamed models)."""
        merged = self._store.load()
        for key, value in values.items():
            if value is None or value == Settings.model_fields[key].default:
                merged.pop(key, None)
            else:
                merged[key] = value
        return merged
