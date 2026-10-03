"""Assembles the whole application from its parts (the "composition root").

Kept separate from __main__ so the wiring can be built and tested in-process, without owning the
event loop, the single-instance lock, global hotkeys or the OS tray."""

import logging
from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QWidget

from interview_assistant.agents.jev import Jev, JevConfig
from interview_assistant.app.controller import AssistantController
from interview_assistant.app.interview_session import InterviewSessionManager
from interview_assistant.app.presentation import PresentationMode
from interview_assistant.app.restart import request_restart
from interview_assistant.audio.types import DeviceKind
from interview_assistant.config.keys import KeyStore
from interview_assistant.config.settings import Settings
from interview_assistant.config.user_settings import UserSettingsStore
from interview_assistant.cost.factory import build_tracker, data_dir
from interview_assistant.cost.tracker import CostTracker
from interview_assistant.history.store import HistoryStore
from interview_assistant.llm.base import LLMProvider
from interview_assistant.llm.factory import build_providers
from interview_assistant.llm.router_factory import build_router
from interview_assistant.privacy.consent import ConsentGate
from interview_assistant.privacy.disclosure import DataFlow, describe_data_flow
from interview_assistant.privacy.state import PrivacyState
from interview_assistant.stt.factory import build_provider
from interview_assistant.stt.metered import MeteredSTT
from interview_assistant.ui.audio_panel import AudioPanel
from interview_assistant.ui.main_window import MainWindow
from interview_assistant.ui.overlay import OverlayWindow
from interview_assistant.ui.privacy_dialog import PrivacyDialog
from interview_assistant.ui.settings_dialog import SettingsDialog
from interview_assistant.ui.tray import TrayController

log = logging.getLogger(__name__)


@dataclass
class AppParts:
    settings: Settings
    tracker: CostTracker
    providers: dict[str, LLMProvider]
    jev: Jev
    history: HistoryStore
    interview_sessions: InterviewSessionManager
    controller: AssistantController
    overlay: OverlayWindow
    window: MainWindow
    privacy: PrivacyState
    presentation: PresentationMode
    privacy_dialog: PrivacyDialog
    settings_dialog: SettingsDialog
    tray: TrayController
    stt_name: str
    toggle_overlay: Callable[[], None]
    open_main: Callable[[], None]
    on_second_launch: Callable[[], None]


def build_app(
    qapp: QApplication,
    settings: Settings,
    keys: KeyStore,
    app_settings: QSettings,
    consent_prompt: Callable[[str], bool],
    confirm_restart: Callable[[QWidget], bool],
    user_settings: UserSettingsStore | None = None,
) -> AppParts:
    """Create every component and connect them. Nothing is shown or started here."""
    tracker = build_tracker(settings)
    providers = build_providers(settings)
    router = build_router(settings, providers, on_attempt=tracker.record_attempt)
    jev = Jev(
        router,
        JevConfig(
            profile=settings.candidate_profile,
            classify_timeout_s=settings.jev_classify_timeout_s,
        ),
    )
    db_path = data_dir(settings) / "history.db"
    history = HistoryStore(db_path, enabled=settings.history_enabled)
    if settings.history_retention_days:
        removed = history.purge_older_than(settings.history_retention_days)
        log.info("Retention purge removed %d old session(s)", removed)
    interview_sessions = InterviewSessionManager(jev.config, history, settings.candidate_profile)
    controller = AssistantController(
        jev, tracker=tracker, history=history, answer_mode=settings.answer_mode
    )
    tracker.on_change = controller.notify_cost_changed
    overlay = OverlayWindow(controller)

    stt = build_provider(settings)
    billed = getattr(stt, "metered", False)
    # Step 19: simultaneous mic + system audio capture is opt-in (2x local-Whisper memory if
    # stt_provider="local") - when on, the primary panel is always loopback (default_audio_kind
    # is moot: there's no longer a single "the" source to pre-select) and a second, mic-only
    # panel+transcriber run alongside it, each explicitly tagged so turns/history can tell them
    # apart ("voice:loopback" / "voice:mic" - see AssistantController.on_transcript_event).
    if settings.dual_audio_capture:
        audio_kind, audio_source = DeviceKind.LOOPBACK, DeviceKind.LOOPBACK.value
        mic_panel: AudioPanel | None = AudioPanel(
            default_kind=DeviceKind.MIC, restrict_kind=DeviceKind.MIC
        )
        mic_provider: MeteredSTT | None = MeteredSTT(build_provider(settings), tracker.record_stt)
    else:
        audio_kind, audio_source = DeviceKind(settings.default_audio_kind), ""
        mic_panel, mic_provider = None, None
    window = MainWindow(
        AudioPanel(default_kind=audio_kind, restrict_kind=audio_kind if mic_panel else None),
        MeteredSTT(stt, tracker.record_stt),
        controller,
        overlay,
        partials=not billed,  # partials re-send the whole utterance: only free for local STT
        history=history,
        interview_sessions=interview_sessions,
        audio_source=audio_source,
        mic_panel=mic_panel,
        mic_provider=mic_provider,
    )

    # ---- privacy: incognito, redaction, presentation mode, consent ----
    privacy = PrivacyState(
        history, tracker, jev.config, incognito=settings.incognito, redact=settings.redact_pii
    )
    hidden: list[QWidget] = [w for w in (overlay, window.history_window) if w is not None]
    audio_stoppables = [window.audio_panel] + ([window.mic_panel] if window.mic_panel else [])
    presentation = PresentationMode(hidden, window, audio_stoppables, controller)

    def flows() -> list[DataFlow]:
        return describe_data_flow(
            stt.name,
            providers.keys(),
            bool(settings.candidate_profile),
            privacy.redact,
            history.enabled,
            privacy.incognito,
            str(db_path.parent),
        )

    hotkey_pairs = (
        (settings.hotkey_presentation, "presentation mode"),
        (settings.hotkey_overlay, "show/hide overlay"),
        (settings.hotkey_bubble, "collapse overlay to a bubble"),
    )
    hint = ", ".join(f"{key} = {what}" for key, what in hotkey_pairs if key)
    privacy_dialog = PrivacyDialog(privacy, presentation, flows, hint)
    hidden.append(privacy_dialog)
    consent = ConsentGate(app_settings, consent_prompt, settings.require_consent)
    window.attach_privacy(privacy, presentation, privacy_dialog, consent)

    # ---- settings dialog: most changes need a restart, which the app offers to do ----
    settings_dialog = SettingsDialog(user_settings or UserSettingsStore(), keys)
    window.attach_settings(settings_dialog)
    hidden.append(settings_dialog)

    def on_saved(needs_restart: bool) -> None:
        if needs_restart and confirm_restart(window):
            request_restart()
            qapp.quit()

    settings_dialog.saved.connect(on_saved)

    # ---- window helpers shared by the tray, hotkeys and second launches ----
    def toggle_overlay() -> None:
        if presentation.active:
            return  # the overlay stays hidden until presentation mode is ended
        overlay.setVisible(not overlay.isVisible())

    def open_main() -> None:
        window.showNormal()
        window.raise_()
        window.activateWindow()

    def on_second_launch() -> None:
        if presentation.active:
            return  # never pop the assistant up while the user is presenting
        open_main()
        overlay.show()

    tray = TrayController(
        toggle_overlay,
        presentation.toggle,
        lambda: privacy.set_incognito(not privacy.incognito),
        open_main,
        qapp.quit,
    )

    def sync_tray(*_args: object) -> None:
        tray.set_state(presentation.active, privacy.incognito)

    presentation.changed.connect(sync_tray)
    privacy.changed.connect(sync_tray)
    sync_tray()

    return AppParts(
        settings=settings,
        tracker=tracker,
        providers=providers,
        jev=jev,
        history=history,
        interview_sessions=interview_sessions,
        controller=controller,
        overlay=overlay,
        window=window,
        privacy=privacy,
        presentation=presentation,
        privacy_dialog=privacy_dialog,
        settings_dialog=settings_dialog,
        tray=tray,
        stt_name=stt.name,
        toggle_overlay=toggle_overlay,
        open_main=open_main,
        on_second_launch=on_second_launch,
    )
