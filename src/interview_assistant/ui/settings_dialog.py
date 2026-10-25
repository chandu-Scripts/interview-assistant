from collections.abc import Callable
from functools import partial
from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from interview_assistant.app.hotkeys import parse_hotkey
from interview_assistant.config.keys import KEY_PROVIDERS, KeyStatus, KeyStore, key_status
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
from interview_assistant.ui.controls import ChoiceComboBox
from interview_assistant.ui.key_row import Checker, KeyRow
from interview_assistant.ui.provider_catalog import MODEL_FIELDS as PROVIDER_FIELDS
from interview_assistant.ui.provider_catalog import NAMES, SUGGESTIONS, ProviderCatalog
from interview_assistant.ui.provider_picker import GlassDialog, update_provider_button
from interview_assistant.ui.relay_panel import RelayPanel, RequestFn, StatusFn

WHISPER_CHOICES = [
    ("tiny.en - fastest, least accurate", "tiny.en"),
    ("base.en - balanced (default)", "base.en"),
    ("small.en - more accurate, ~3x slower", "small.en"),
    ("medium.en - most accurate, slow on CPU", "medium.en"),
]
MODEL_PROVIDER = {
    field: provider for provider, fields in PROVIDER_FIELDS.items() for field in fields
}


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
        catalog: ProviderCatalog | None = None,
    ) -> None:
        super().__init__()
        self._store = store
        self._keys = keys
        self._raw = raw_settings
        self.catalog = catalog or ProviderCatalog(
            raw_settings,
            keys,
            store.path.parent / "provider_models.json",
            can_write=lambda: not self._raw().incognito,
        )
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
        # Each tab scrolls independently; hidden tabs must not widen the whole workspace.
        for index in range(self.tabs.count()):
            content = self.tabs.widget(index)
            assert content is not None
            title = self.tabs.tabText(index)
            self.tabs.removeTab(index)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setWidget(content)
            self.tabs.insertTab(index, scroll, title)
        self.tabs.setCurrentIndex(0)
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
        self.catalog.changed.connect(self._refresh_providers)
        self._refresh_providers()

    # ---- tabs ----
    def _providers_tab(self, checker: Checker, runner: Runner) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        note = QLabel(
            "Choose a provider to manage its key and Fast / Smart models. "
            "Green: key verified. Red: missing or rejected. Amber: test needed or connection issue."
        )
        note.setWordWrap(True)
        lay.addWidget(note)
        self.provider_buttons: dict[str, QPushButton] = {}
        self.provider_dialogs: dict[str, GlassDialog] = {}
        self.provider_states: dict[str, QLabel] = {}
        self.model_boxes: dict[str, ChoiceComboBox] = {}
        for p in KEY_PROVIDERS:
            row = KeyRow(
                p,
                self._keys,
                self._status_fn(p),
                checker,
                runner,
                key_getter=partial(self.catalog.key, p),
                preferences=self._store,
            )
            row.changed.connect(lambda provider=p: self._on_provider_key_changed(provider))
            row.tested.connect(self.catalog.record)
            self.key_rows[p] = row
            if p == "speechmatics":
                lay.addWidget(row)
                note = QLabel("Select Speechmatics in the Speech tab to enable live transcription.")
                note.setWordWrap(True)
                lay.addWidget(note)
                continue
            popup = GlassDialog(NAMES[p], self)
            self.provider_dialogs[p] = popup
            state = QLabel()
            state.setWordWrap(True)
            self.provider_states[p] = state
            popup.body.addWidget(state)
            popup.body.addWidget(row)
            model_note = QLabel(
                "Recommended: Fast — gpt-5.4-mini; Smart — gpt-5.4. "
                "Only these two models are listed. Existing selections stay until you save."
                if p == "openai"
                else "Fast and Smart are your routing presets. Select an actual model for each. "
                "Test key refreshes the same account model list used in Preferred model."
            )
            model_note.setWordWrap(True)
            popup.body.addWidget(model_note)
            model_form = QFormLayout()
            model_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
            for tier, field in zip(("Fast", "Smart"), PROVIDER_FIELDS[p], strict=True):
                box = ChoiceComboBox()
                box.setEditable(True)
                box.addItems(SUGGESTIONS.get(p, []))
                self.model_boxes[field] = box
                model_form.addRow(tier, box)
            popup.body.addLayout(model_form)
            if p == "openai":
                self.recommended_models_btn = QPushButton("Use recommended models")
                self.recommended_models_btn.clicked.connect(self._use_recommended_models)
                popup.body.addWidget(self.recommended_models_btn)
            note = QLabel(
                "Test key checks authentication only; credits and model access may vary. "
                "Keys save immediately. Close this popup and use Settings → Save "
                "to apply model changes."
            )
            note.setWordWrap(True)
            popup.body.addWidget(note)
            popup.body.addStretch(1)
            button = QPushButton()
            button.clicked.connect(lambda _checked=False, provider=p: self._open_provider(provider))
            self.provider_buttons[p] = button
            lay.addWidget(button)
        form = QFormLayout()
        self.router_fast = QLineEdit()
        self.router_fast.setPlaceholderText(
            "auto (e.g. openai:gpt-4o-mini,gemini:gemini-flash-latest)"
        )
        self.router_smart = QLineEdit()
        self.router_smart.setPlaceholderText("auto")
        form.addRow("Fast chain (advanced)", self.router_fast)
        form.addRow("Smart chain (advanced)", self.router_smart)
        advanced = QWidget()
        advanced.setLayout(form)
        advanced.hide()
        advanced_button = QPushButton("Advanced routing")
        advanced_button.setCheckable(True)
        advanced_button.toggled.connect(advanced.setVisible)
        lay.addWidget(advanced_button)
        lay.addWidget(advanced)
        lay.addStretch(1)
        return w

    def _open_provider(self, provider: str) -> None:
        self.key_rows[provider].refresh()
        self._refresh_providers()
        self.provider_dialogs[provider].present()

    def _use_recommended_models(self) -> None:
        for field, model in zip(PROVIDER_FIELDS["openai"], SUGGESTIONS["openai"], strict=True):
            self.model_boxes[field].setCurrentText(model)
        self.message.setText("Recommended models selected. Save settings and restart to apply.")

    def close_provider_popups(self) -> None:
        for popup in self.provider_dialogs.values():
            popup.reject()

    def _on_provider_key_changed(self, provider: str) -> None:
        if provider in NAMES:
            self.catalog.invalidate(provider)
        self._on_key_changed()

    def _refresh_providers(self) -> None:
        for provider, button in self.provider_buttons.items():
            update_provider_button(button, provider, self.catalog)
            state, detail = self.catalog.status(provider)
            self.provider_states[provider].setText(
                detail
                + ("\n" + self.catalog.persistence_error if self.catalog.persistence_error else "")
            )
            self._fill_models(
                provider,
                self.catalog.models(provider)
                if state == "verified"
                else SUGGESTIONS.get(provider, []),
            )
        verified = self.catalog.status("openai")[0] == "verified"
        available = self.catalog.models("openai")
        self.recommended_models_btn.setEnabled(
            not verified or all(model in available for model in SUGGESTIONS["openai"])
        )
        self.recommended_models_btn.setToolTip(
            "Set Fast to gpt-5.4-mini and Smart to gpt-5.4. Save settings to apply."
            if self.recommended_models_btn.isEnabled()
            else "Both recommended models must be listed by your account."
        )

    def _speech_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        self.stt_provider = ChoiceComboBox()
        self.stt_provider.addItem("Local Whisper - offline, free, private", "local")
        self.stt_provider.addItem("OpenAI cloud - sends audio to OpenAI, billed", "openai")
        self.stt_provider.addItem(
            "Speechmatics live - cloud, billed; OpenAI fallback if configured, else local",
            "speechmatics",
        )
        self.whisper_model = ChoiceComboBox()
        for label, value in WHISPER_CHOICES:
            self.whisper_model.addItem(label, value)
        self.stt_language = QLineEdit()
        self.stt_language.setPlaceholderText("en (leave empty to auto-detect)")
        self.stt_vocabulary = QLineEdit()
        self.stt_vocabulary.setPlaceholderText("terms the recognizer should expect (empty = off)")
        self.default_audio_kind = ChoiceComboBox()
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
        self.budget_scope = ChoiceComboBox()
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
        self.hotkey_screenshot = QLineEdit()
        self.hotkey_chat = QLineEdit()
        for e in (
            self.hotkey_presentation,
            self.hotkey_overlay,
            self.hotkey_bubble,
            self.hotkey_answer,
            self.hotkey_screenshot,
            self.hotkey_chat,
        ):
            e.setPlaceholderText("e.g. ctrl+alt+p (empty = disabled)")
        form.addRow(self.incognito)
        form.addRow(self.redact)
        form.addRow(self.consent)
        form.addRow("Hotkey: presentation mode", self.hotkey_presentation)
        form.addRow("Hotkey: show/hide overlay", self.hotkey_overlay)
        form.addRow("Hotkey: collapse to bubble", self.hotkey_bubble)
        form.addRow("Hotkey: answer from any window", self.hotkey_answer)
        form.addRow("Hotkey: screenshot from any window", self.hotkey_screenshot)
        form.addRow("Hotkey: show/hide chat from any window", self.hotkey_chat)
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
        self.hotkey_screenshot.setText(s.hotkey_screenshot)
        self.hotkey_chat.setText(s.hotkey_chat)
        for row in self.key_rows.values():
            row.refresh()
        self.relay_panel.refresh()
        # Missing credentials must not prevent opening the page that lets the user add them.
        self._initial = self.collect(validate_keys=False)
        self._refresh_providers()

    @staticmethod
    def _select(combo: ChoiceComboBox, value: str) -> None:
        i = combo.findData(value)
        combo.setCurrentIndex(max(i, 0))

    @staticmethod
    def _select_or_add(combo: ChoiceComboBox, value: str) -> None:
        if combo.findData(value) < 0:
            combo.addItem(value, value)
        combo.setCurrentIndex(combo.findData(value))

    def collect(self, *, validate_keys: bool = True) -> dict[str, Any]:
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
            ("hotkey_screenshot", self.hotkey_screenshot),
            ("hotkey_chat", self.hotkey_chat),
        ):
            spec = edit.text().strip()
            if spec:
                try:
                    parse_hotkey(spec)
                except ValueError as e:
                    raise ValueError(str(e)) from e
            values[field] = spec
        if (
            validate_keys
            and values["stt_provider"] == "openai"
            and not key_status("openai", self._raw(), self._keys).present
        ):
            raise ValueError("Cloud speech-to-text needs an OpenAI key (AI providers tab).")
        if (
            validate_keys
            and values["stt_provider"] == "speechmatics"
            and not key_status("speechmatics", self._raw(), self._keys).present
        ):
            raise ValueError("Live transcription needs a Speechmatics key (AI providers tab).")
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
        self.catalog.changed.emit()
        self.saved.emit(needs_restart)
        self.accept()

    def hideEvent(self, event) -> None:  # noqa: N802
        self.close_provider_popups()
        super().hideEvent(event)

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
