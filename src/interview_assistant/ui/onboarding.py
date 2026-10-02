from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWizard,
    QWizardPage,
)

from interview_assistant.config.keys import PROVIDERS, KeyStatus, KeyStore, key_status
from interview_assistant.config.provider_check import check_key
from interview_assistant.config.settings import Settings
from interview_assistant.privacy.consent import KEY as CONSENT_KEY
from interview_assistant.privacy.disclosure import CONSENT_TEXT
from interview_assistant.ui.bg import Runner, run_in_thread
from interview_assistant.ui.key_row import Checker, KeyRow
from interview_assistant.ui.settings_dialog import WHISPER_CHOICES

DONE_KEY = "onboarding/done_v1"
WarmupFn = Callable[[str], None]


def needs_onboarding(qsettings: QSettings) -> bool:
    return not qsettings.value(DONE_KEY, False, type=bool)


class WelcomePage(QWizardPage):
    def __init__(self) -> None:
        super().__init__()
        self.setTitle("Welcome")
        intro = QLabel(
            "Interview Assistant listens to a conversation, transcribes it, and streams a "
            "suggested answer into a small overlay. This short setup takes about two minutes.\n\n"
            "Before you start:"
        )
        intro.setWordWrap(True)
        notice = QLabel(CONSENT_TEXT)
        notice.setWordWrap(True)
        self.agree = QCheckBox("I understand and will use this responsibly")
        lay = QVBoxLayout(self)
        lay.addWidget(intro)
        lay.addWidget(notice)
        lay.addWidget(self.agree)
        self.agree.toggled.connect(lambda _c: self.completeChanged.emit())

    def isComplete(self) -> bool:
        return self.agree.isChecked()


class ProvidersPage(QWizardPage):
    def __init__(
        self,
        store: KeyStore,
        raw_settings: Callable[[], Settings],
        checker: Checker,
        runner: Runner,
    ) -> None:
        super().__init__()
        self.setTitle("AI providers")
        self.setSubTitle(
            "Add at least one API key. Keys are stored encrypted in the Windows Credential "
            "Manager. Use Test to check a key (it is free), then Save."
        )
        lay = QVBoxLayout(self)
        self.rows: dict[str, KeyRow] = {}
        self._store, self._raw = store, raw_settings
        for p in PROVIDERS:
            row = KeyRow(p, store, self._status_fn(p), checker, runner)
            row.changed.connect(lambda: self.completeChanged.emit())
            self.rows[p] = row
            lay.addWidget(row)
        self.skip = QCheckBox("Skip for now (answers will not work until a key is added)")
        self.skip.toggled.connect(lambda _c: self.completeChanged.emit())
        lay.addWidget(self.skip)

    def _status_fn(self, provider: str) -> Callable[[], KeyStatus]:
        return lambda: key_status(provider, self._raw(), self._store)

    def has_openai_key(self) -> bool:
        return key_status("openai", self._raw(), self._store).present

    def has_key(self) -> bool:
        return any(key_status(p, self._raw(), self._store).present for p in PROVIDERS)

    def isComplete(self) -> bool:
        return self.has_key() or self.skip.isChecked()


class SpeechPage(QWizardPage):
    def __init__(
        self, has_openai_key: Callable[[], bool], warmup: WarmupFn, runner: Runner
    ) -> None:
        super().__init__()
        self.setTitle("Speech recognition")
        self.setSubTitle("How should the conversation be turned into text?")
        self._has_openai = has_openai_key
        self._warmup = warmup
        self._runner = runner
        self.local = QRadioButton("On this PC (Whisper) - free, private, works offline")
        self.cloud = QRadioButton("OpenAI cloud - audio is sent to OpenAI and billed per minute")
        self.local.setChecked(True)
        self.model = QComboBox()
        for label, value in WHISPER_CHOICES:
            self.model.addItem(label, value)
        self.model.setCurrentIndex(self.model.findData("base.en"))
        self.download = QPushButton("Download / check the model now")
        self.status = QLabel("The model downloads once (about 150 MB for base.en).")
        self.status.setWordWrap(True)
        self.source = QComboBox()
        self.source.addItem("System audio (the interviewer's voice)", "loopback")
        self.source.addItem("Microphone", "mic")
        lay = QVBoxLayout(self)
        for w in (self.local, self.model, self.download, self.status, self.cloud):
            lay.addWidget(w)
        lay.addWidget(QLabel("Pre-selected audio source:"))
        lay.addWidget(self.source)
        self.download.clicked.connect(self._download)
        self.local.toggled.connect(self._sync)

    def initializePage(self) -> None:
        self.cloud.setEnabled(self._has_openai())
        if not self.cloud.isEnabled():
            self.cloud.setToolTip("Add an OpenAI key on the previous page to enable this.")
        self._sync()

    def _sync(self) -> None:
        self.model.setEnabled(self.local.isChecked())
        self.download.setEnabled(self.local.isChecked())

    def _download(self) -> None:
        self.status.setText("Downloading… this can take a minute on the first run.")
        self.download.setEnabled(False)
        model = self.model.currentData()

        def done(result: object) -> None:
            self.download.setEnabled(self.local.isChecked())
            if isinstance(result, Exception):
                self.status.setText(
                    f"✖ Could not load the model: {type(result).__name__}. Check your connection."
                )
            else:
                self.status.setText("✔ Model ready.")

        self._runner(lambda: self._warmup(model), done)


class ProfilePage(QWizardPage):
    def __init__(self) -> None:
        super().__init__()
        self.setTitle("About you (optional)")
        self.setSubTitle("This background is sent with each question so answers fit you.")
        self.profile = QPlainTextEdit()
        self.profile.setPlaceholderText(
            "e.g. Backend engineer, 5 years Python/Go, payments platform…"
        )
        self.budget_on = QCheckBox("Limit spending to")
        self.budget = QDoubleSpinBox()
        self.budget.setRange(0.01, 10000)
        self.budget.setPrefix("$")
        self.budget.setValue(5.0)
        self.budget_scope = QComboBox()
        for label, value in (
            ("per session", "session"),
            ("per day", "day"),
            ("per month", "month"),
        ):
            self.budget_scope.addItem(label, value)
        self.incognito = QCheckBox("Start in incognito mode (save no history)")
        lay = QVBoxLayout(self)
        lay.addWidget(self.profile, 1)
        lay.addWidget(self.budget_on)
        lay.addWidget(self.budget)
        lay.addWidget(self.budget_scope)
        lay.addWidget(self.incognito)


class OnboardingWizard(QWizard):
    """First-run setup. Keys are saved as they are entered; everything else is returned by
    collect() and written by the caller once the wizard is accepted."""

    def __init__(
        self,
        keys: KeyStore,
        raw_settings: Callable[[], Settings] = Settings,
        checker: Checker = check_key,
        runner: Runner = run_in_thread,
        warmup: WarmupFn | None = None,
    ) -> None:
        super().__init__()
        self.setWindowTitle("Interview Assistant - setup")
        self.welcome = WelcomePage()
        self.providers = ProvidersPage(keys, raw_settings, checker, runner)
        self.speech = SpeechPage(self.providers.has_openai_key, warmup or _default_warmup, runner)
        self.profile = ProfilePage()
        for page in (self.welcome, self.providers, self.speech, self.profile):
            self.addPage(page)

    def collect(self) -> dict[str, Any]:
        sp, pr = self.speech, self.profile
        return {
            "stt_provider": "openai" if sp.cloud.isChecked() else "local",
            "whisper_model": sp.model.currentData(),
            "default_audio_kind": sp.source.currentData(),
            "candidate_profile": pr.profile.toPlainText().strip() or None,
            "budget_usd": pr.budget.value() if pr.budget_on.isChecked() else None,
            "budget_scope": pr.budget_scope.currentData(),
            "incognito": pr.incognito.isChecked(),
        }

    def finish(self, qsettings: QSettings) -> None:
        """Remember that setup is done and that the recording notice was acknowledged."""
        qsettings.setValue(DONE_KEY, True)
        if self.welcome.agree.isChecked():
            qsettings.setValue(CONSENT_KEY, True)


def _default_warmup(model: str) -> None:
    from interview_assistant.stt.local_whisper import FasterWhisperProvider

    FasterWhisperProvider(model).warmup()
