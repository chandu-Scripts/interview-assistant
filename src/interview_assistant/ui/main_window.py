import threading

from PySide6.QtCore import Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMainWindow, QPushButton, QVBoxLayout, QWidget

from interview_assistant import __version__
from interview_assistant.app.controller import AssistantController
from interview_assistant.app.interview_session import InterviewSessionManager
from interview_assistant.app.presentation import PresentationMode
from interview_assistant.audio.types import DeviceKind
from interview_assistant.cost.tracker import format_usd
from interview_assistant.history.store import HistoryStore
from interview_assistant.privacy.consent import ConsentGate
from interview_assistant.privacy.state import PrivacyState
from interview_assistant.stt.base import STTProvider
from interview_assistant.stt.streaming import StreamingTranscriber
from interview_assistant.ui.audio_panel import AudioPanel
from interview_assistant.ui.history_window import HistoryWindow
from interview_assistant.ui.overlay import OverlayWindow
from interview_assistant.ui.privacy_dialog import PrivacyDialog
from interview_assistant.ui.settings_dialog import SettingsDialog
from interview_assistant.ui.transcript_panel import TranscriptPanel


class MainWindow(QMainWindow):
    """Control window: audio source, live transcript. Answers appear in the overlay."""

    # Step 19: a second, simultaneous audio source's transcript events, relayed via a queued
    # signal exactly like TranscriptPanel.event_received does for the primary source - emitted
    # from the mic's STT worker thread, so it must be a signal (not a direct call) to reach the
    # controller safely on the UI thread. NOT routed through TranscriptPanel itself: that widget
    # keys its state by utterance_id, and the mic transcriber has its own id sequence starting at
    # 1 - sharing the panel would silently collide two unrelated utterances onto one id.
    mic_transcript_received = Signal(object)

    def __init__(
        self,
        audio_panel: AudioPanel | None = None,
        provider: STTProvider | None = None,
        controller: AssistantController | None = None,
        overlay: OverlayWindow | None = None,
        partials: bool = True,
        history: HistoryStore | None = None,
        interview_sessions: InterviewSessionManager | None = None,
        audio_source: str = "",
        mic_panel: AudioPanel | None = None,
        mic_provider: STTProvider | None = None,
    ) -> None:
        super().__init__()
        self.setWindowTitle("Interview Assistant")
        self.resize(640, 480)
        self.label = QLabel(f"Interview Assistant v{__version__} — ready")
        self.label.setObjectName("status_label")
        self.audio_panel = audio_panel or AudioPanel()
        # Step 19: an optional second, simultaneous source (e.g. "mic" alongside the primary
        # "loopback"). `audio_source` tags the PRIMARY panel's questions ("" keeps today's plain
        # "voice" source string unchanged; a real tag like "loopback" is only used once a second
        # source actually exists, so single-source installs are byte-for-byte unaffected).
        self.mic_panel = mic_panel
        self._audio_source = audio_source
        self.transcript_panel = TranscriptPanel()
        self.transcriber: StreamingTranscriber | None = None
        self.mic_transcriber: StreamingTranscriber | None = None
        self.controller = controller
        self.overlay = overlay
        self.overlay_button = QPushButton("Show overlay")
        self.clear_button = QPushButton("New interview")
        self.clear_button.setToolTip("Clear the conversation context and start a new cost session")
        self.cost_label = QLabel("")
        self.history_button = QPushButton("History")
        self.history_window = (
            HistoryWindow(history, session_manager=interview_sessions)
            if history is not None
            else None
        )
        self.settings_button = QPushButton("Settings")
        self.settings_button.setEnabled(False)  # enabled by attach_settings()
        self.settings_dialog: SettingsDialog | None = None
        self.privacy_button = QPushButton("Privacy")
        self.privacy_button.setEnabled(False)  # enabled by attach_privacy()
        self.banner = QLabel("")
        self.banner.setObjectName("banner")
        self.banner.setVisible(False)
        self.privacy_dialog: PrivacyDialog | None = None
        self._privacy: PrivacyState | None = None
        self._presentation: PresentationMode | None = None
        top = QHBoxLayout()
        top.addWidget(self.label, 1)
        top.addWidget(self.cost_label)
        top.addWidget(self.settings_button)
        top.addWidget(self.privacy_button)
        top.addWidget(self.history_button)
        top.addWidget(self.clear_button)
        top.addWidget(self.overlay_button)
        central = QWidget()
        layout = QVBoxLayout(central)
        layout.addWidget(self.banner)
        layout.addLayout(top)
        layout.addWidget(self.audio_panel)
        if self.mic_panel is not None:
            layout.addWidget(self.mic_panel)
        layout.addWidget(self.transcript_panel, 1)
        self.setCentralWidget(central)

        self.overlay_button.clicked.connect(self.show_overlay)
        self.history_button.setEnabled(self.history_window is not None)
        self.history_button.clicked.connect(self.show_history)
        self.overlay_button.setEnabled(overlay is not None)
        self.clear_button.setEnabled(controller is not None)
        if controller is not None:
            self.clear_button.clicked.connect(controller.new_interview)
            controller.cost_updated.connect(self._on_cost)
            if self.history_window is not None:
                controller.busy_changed.connect(self._refresh_history)
                self.clear_button.clicked.connect(self._refresh_history)
            self.audio_panel.speech_changed.connect(
                lambda on: controller.on_speech_started(audio_source)
                if on
                else controller.on_speech_ended()
            )
            self.transcript_panel.event_received.connect(controller.on_transcript_event)
            self.mic_transcript_received.connect(controller.on_transcript_event)
            if self.mic_panel is not None:
                self.mic_panel.speech_changed.connect(
                    lambda on: controller.on_speech_started(DeviceKind.MIC.value)
                    if on
                    else controller.on_speech_ended()
                )
        if overlay is not None:
            if self.mic_panel is not None:
                # Either source recording counts as "listening" - a naive connect-both-to-
                # set_listening would have the LAST signal silently win and show "idle" while the
                # other source is still actively capturing.
                mic_panel = self.mic_panel
                self.audio_panel.recording_changed.connect(
                    lambda _on: overlay.set_listening(
                        self.audio_panel.recording or mic_panel.recording
                    )
                )
                mic_panel.recording_changed.connect(
                    lambda _on: overlay.set_listening(
                        self.audio_panel.recording or mic_panel.recording
                    )
                )
            else:
                self.audio_panel.recording_changed.connect(overlay.set_listening)
        self._partials = partials
        if provider is not None:
            self._attach(provider)
        if mic_provider is not None and self.mic_panel is not None:
            self._attach_mic(mic_provider)

    def attach_privacy(
        self,
        state: PrivacyState,
        presentation: PresentationMode,
        dialog: PrivacyDialog,
        consent: ConsentGate | None = None,
    ) -> None:
        self._privacy, self._presentation, self.privacy_dialog = state, presentation, dialog
        self.privacy_button.setEnabled(True)
        self.privacy_button.clicked.connect(self.show_privacy)
        state.changed.connect(self._update_banner)
        presentation.changed.connect(lambda _on: self._update_banner())
        if consent is not None:
            self.audio_panel.before_start = consent.ensure
            if self.mic_panel is not None:
                self.mic_panel.before_start = consent.ensure
        self._update_banner()

    def attach_settings(self, dialog: SettingsDialog) -> None:
        self.settings_dialog = dialog
        self.settings_button.setEnabled(True)
        self.settings_button.clicked.connect(self.show_settings)

    def show_settings(self) -> None:
        if self.settings_dialog is not None:
            self.settings_dialog.load()
            self.settings_dialog.show()
            self.settings_dialog.raise_()

    def show_privacy(self) -> None:
        if self.privacy_dialog is not None:
            self.privacy_dialog.refresh()
            self.privacy_dialog.show()
            self.privacy_dialog.raise_()

    def _update_banner(self) -> None:
        if self._presentation is not None and self._presentation.active:
            text, color = (
                "PRESENTATION MODE - assistant hidden, listening and answering paused",
                "#d32f2f",
            )
        elif self._privacy is not None and self._privacy.incognito:
            text, color = "INCOGNITO - nothing is being saved", "#7a5cf5"
        else:
            self.banner.setVisible(False)
            return
        self.banner.setText(text)
        self.banner.setStyleSheet(
            f"background:{color}; color:white; font-weight:bold; padding:4px; border-radius:4px;"
        )
        self.banner.setVisible(True)

    def _on_cost(self, summary, state) -> None:
        self.cost_label.setText(
            f"Session cost: {format_usd(summary.total_usd, summary.unpriced_calls > 0)}"
        )

    def show_history(self) -> None:
        if self.history_window is not None:
            self.history_window.refresh()
            self.history_window.show()
            self.history_window.raise_()

    def _refresh_history(self, *_args: object) -> None:
        if self.history_window is not None and self.history_window.isVisible():
            self.history_window.refresh()

    def show_overlay(self) -> None:
        if self.overlay is not None:
            self.overlay.show()
            self.overlay.raise_()

    def _attach(self, provider: STTProvider) -> None:
        panel = self.transcript_panel
        transcriber = StreamingTranscriber(
            provider,
            on_event=panel.event_received.emit,  # worker thread -> queued to UI thread
            on_error=lambda e: panel.error_received.emit(str(e)),
            partials=self._partials,
            # None (the default, when audio_source == "") keeps a single-source install's
            # questions tagged plain "voice", exactly as before Step 19.
            source=DeviceKind(self._audio_source) if self._audio_source else None,
        )
        # Audio signals arrive on the UI thread in order: state(True), partials..., segment.
        ap = self.audio_panel
        ap.speech_changed.connect(lambda on: transcriber.speech_started() if on else None)
        ap.partial_ready.connect(transcriber.submit_partial)
        ap.segment_ready.connect(transcriber.submit_final)
        transcriber.start()
        self.transcriber = transcriber
        threading.Thread(target=provider.warmup, name="stt-warmup", daemon=True).start()

    def _attach_mic(self, provider: STTProvider) -> None:
        """The second, simultaneous source (Step 19). Deliberately NOT wired through
        TranscriptPanel - see mic_transcript_received's docstring for why."""
        assert self.mic_panel is not None
        transcriber = StreamingTranscriber(
            provider,
            on_event=self.mic_transcript_received.emit,  # worker thread -> queued to UI thread
            on_error=lambda e: self.transcript_panel.error_received.emit(str(e)),
            partials=self._partials,
            source=DeviceKind.MIC,
        )
        ap = self.mic_panel
        ap.speech_changed.connect(lambda on: transcriber.speech_started() if on else None)
        ap.partial_ready.connect(transcriber.submit_partial)
        ap.segment_ready.connect(transcriber.submit_final)
        transcriber.start()
        self.mic_transcriber = transcriber
        threading.Thread(target=provider.warmup, name="stt-warmup-mic", daemon=True).start()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        self.audio_panel.stop()
        if self.transcriber is not None:
            self.transcriber.stop()
        if self.mic_panel is not None:
            self.mic_panel.stop()
        if self.mic_transcriber is not None:
            self.mic_transcriber.stop()
        if self.controller is not None:
            self.controller.shutdown()
        if self.overlay is not None:
            self.overlay.close()
        if self.history_window is not None:
            self.history_window.close()
        if self.privacy_dialog is not None:
            self.privacy_dialog.close()
        if self.settings_dialog is not None:
            self.settings_dialog.close()
        super().closeEvent(event)
