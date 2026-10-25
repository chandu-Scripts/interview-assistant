"""The whole application assembled in-process (everything except the event loop, hotkeys, the OS
tray and the single-instance lock, which belong to main())."""

import time
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QLabel

from interview_assistant.app import restart
from interview_assistant.app.composition import AppParts, build_app
from interview_assistant.config.keys import MemoryKeyStore
from interview_assistant.config.settings import Settings
from interview_assistant.config.user_settings import UserSettingsStore
from interview_assistant.history.store import HistoryStore, TurnRecord


class FakeSTT:
    name = "fake-stt"
    metered = False

    def warmup(self):
        pass

    def transcribe(self, pcm):
        return ""


class BilledSTT(FakeSTT):
    name = "cloud-stt"
    metered = True


class StubApp:
    def __init__(self):
        self.quit_calls = 0

    def quit(self):
        self.quit_calls += 1


def test_dashboard_opens_post_call_review_and_presentation_pauses_it(parts_factory):
    parts = parts_factory()
    sid = parts.history.create_interview_session(company="Review company")
    parts.history.end_interview_session(sid)
    parts.start_screen._view_session(sid)
    page = parts.start_screen.review_page
    assert parts.start_screen.pages.currentWidget() is page
    assert page.title.text() == "Review company"
    parts.presentation.activate()
    assert page._controller._paused


def test_navigation_reuses_one_workspace_and_settings_can_be_reopened(parts_factory, qtbot):
    p = parts_factory()
    screen = p.start_screen
    p.overlay.hide()
    before = screen.size()
    for key, button, widget in (
        ("settings", screen.extra_nav["settings"], p.settings_dialog),
        ("privacy", screen.extra_nav["privacy"], p.privacy_dialog),
        ("history", screen.dashboard_btn, p.window.history_window),
        ("controls", screen.extra_nav["controls"], p.window.audio_panel),
    ):
        button.click()
        qtbot.waitUntil(lambda w=widget: w.isVisible())
        assert widget.window() is screen and not widget.isWindow()
        assert screen.pages.currentWidget() is screen._workspace_pages[key]
        assert screen.size() == before
        assert not p.window.isVisible()
    p.window.show_settings()
    p.settings_dialog.cancel_btn.click()
    assert screen.pages.currentIndex() == 0
    p.window.show_settings()
    assert p.settings_dialog.isVisible()
    p.settings_dialog.save_btn.click()
    assert screen.pages.currentIndex() == 0


def test_resume_privacy_notice_and_management_after_explicit_toggle(parts_factory, qtbot, tmp_path):
    p = parts_factory()
    page = p.start_screen.resume_page
    p.privacy.set_incognito(True)
    p.start_screen.resumes_btn.click()
    assert page.write_notice.isVisible()
    assert not page.import_btn.isEnabled()
    assert not page.delete_btn.isEnabled()
    page.privacy_btn.click()
    assert p.privacy_dialog.isVisible()
    assert p.privacy_dialog.window() is p.start_screen
    assert p.privacy.incognito
    p.privacy.set_incognito(False)
    p.start_screen.resumes_btn.click()
    assert not page.write_notice.isVisible()
    assert page.import_btn.isEnabled()
    path = tmp_path / "fictional.txt"
    path.write_text("Fictional engineer", encoding="utf-8")
    page.import_files([path])
    qtbot.waitUntil(lambda: page._worker is None)
    assert page.selected_name() == path.name
    assert page.preview.toPlainText() == "Fictional engineer"
    p.privacy.set_incognito(True)
    assert not page.rename_btn.isEnabled()
    assert not page.delete_btn.isEnabled()
    assert page.preview.toPlainText() == "Fictional engineer"
    p.privacy.set_incognito(False)
    assert page.delete_btn.isEnabled()
    page._confirm_delete = lambda name: True
    page.delete_btn.click()
    assert page.library.list() == []
    assert path.read_text(encoding="utf-8") == "Fictional engineer"


def test_history_create_uses_existing_wizard(parts_factory):
    p = parts_factory()
    p.window.show_history()
    p.window.history_window.create_session_btn.click()
    assert p.start_screen.pages.currentWidget() is p.start_screen.create_page
    assert p.window.history_window._session_dialog is None


def test_practice_bank_stays_in_workspace_and_respects_privacy(parts_factory):
    p = parts_factory()
    screen = p.start_screen
    screen.extra_nav["practice"].click()
    page = screen._workspace_widgets["practice"]
    assert page.isVisible() and page.window() is screen
    assert screen.pages.currentWidget() is screen._workspace_pages["practice"]
    p.privacy.set_incognito(True)
    assert not page.save_btn.isEnabled()
    page.privacy_btn.click()
    assert p.privacy_dialog.isVisible()
    p.presentation.activate()
    previous = screen.pages.currentWidget()
    screen.extra_nav["practice"].click()
    assert screen.isMinimized() and screen.pages.currentWidget() is previous


def test_presentation_hides_embedded_privacy_and_blocks_navigation(parts_factory):
    p = parts_factory()
    p.window.show_privacy()
    assert p.privacy_dialog.window() is p.start_screen
    p.privacy_dialog.presentation_btn.click()
    assert p.start_screen.isMinimized() and p.presentation.active
    previous = p.start_screen.pages.currentWidget()
    p.window.show_settings()
    p.open_main()
    assert p.start_screen.isMinimized()
    assert p.start_screen.pages.currentWidget() is previous
    p.presentation.deactivate()
    assert not p.start_screen.isMinimized()
    assert p.privacy_dialog.isVisible()


@pytest.mark.parametrize(
    "options, expected",
    [
        (
            {"relay_url": "https://relay.example", "relay_token": "test-token"},
            ["", "relay:fast", "relay:smart"],
        ),
        (
            {
                "openai_api_key": "test-key",
                "router_fast": "openai:custom",
                "router_smart": "openai:custom",
            },
            [""],  # configured keys/models are not selectable until verified
        ),
    ],
)
def test_wizard_models_follow_configured_routes(parts_factory, options, expected):
    parts = parts_factory(**options)
    combo = parts.start_screen.create_page.model_override
    assert [combo.itemData(i) for i in range(combo.count())] == expected


def test_provider_and_preferred_model_popups_close_for_presentation(parts_factory, qtbot):
    p = parts_factory()
    p.window.show_settings()
    p.settings_dialog.provider_buttons["openai"].click()
    popup = p.settings_dialog.provider_dialogs["openai"]
    assert popup.isVisible()
    p.presentation.activate()
    assert not popup.isVisible()
    p.presentation.deactivate()
    assert not popup.isVisible()
    p.start_screen._open_create()
    wizard = p.start_screen.create_page
    wizard.company.setText("Example")
    wizard.next_btn.click()
    wizard.model_override.showPopup()
    picker = wizard.model_override.picker
    assert picker.isVisible()
    p.presentation.activate()
    assert not picker.isVisible()
    p.presentation.deactivate()
    assert not picker.isVisible()


def test_composition_shares_verified_catalog_with_wizard(parts_factory, monkeypatch):
    from interview_assistant.config.provider_check import CheckResult
    from interview_assistant.ui.provider_catalog import fingerprint

    p = parts_factory(openai_api_key="runtime-test-key")
    monkeypatch.setenv("OPENAI_API_KEY", "runtime-test-key")
    catalog = p.settings_dialog.catalog
    catalog.record(
        "openai", fingerprint("runtime-test-key"), CheckResult(True, "ok", ["gpt-5.4-mini"])
    )
    wizard = p.start_screen.create_page
    wizard.company.setText("Example")
    selector = wizard.model_override
    assert selector.catalog is catalog
    selector.setCurrentIndex(selector.findData("openai:gpt-5.4-mini"))
    details = wizard.collect()
    assert details.fast_model_route == "openai:gpt-5.4-mini"
    p.interview_sessions.activate(details)
    assert p.jev.config.fast_model_route == "openai:gpt-5.4-mini"
    assert wizard.coding_model_override.catalog is catalog


@pytest.fixture
def parts_factory(qtbot, tmp_path, monkeypatch):
    built: list[AppParts] = []
    monkeypatch.setattr("interview_assistant.app.composition.build_provider", lambda s: FakeSTT())
    monkeypatch.setattr(
        "interview_assistant.app.composition.build_preview_provider", lambda s: FakeSTT()
    )
    for k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.chdir(tmp_path)  # no project .env

    def make(
        consent=lambda text: True, restart_ok=lambda w: True, qapp=None, stt=None, **overrides
    ):
        if stt is not None:
            monkeypatch.setattr("interview_assistant.app.composition.build_provider", lambda s: stt)
        settings = Settings(**{"usage_log": False, **overrides})
        app_settings = QSettings(str(tmp_path / "app.ini"), QSettings.Format.IniFormat)
        parts = build_app(
            qapp or StubApp(),
            settings,
            MemoryKeyStore(),
            app_settings,
            consent,
            restart_ok,
            UserSettingsStore(tmp_path / "settings.json"),
        )
        for w in (parts.window, parts.start_screen, parts.overlay):
            qtbot.addWidget(w)
            if w is not parts.window:
                w.show()
        built.append(parts)
        return parts

    yield make
    for p in built:
        p.window.audio_panel.stop()
        p.controller.shutdown()
        if p.window.transcriber:
            p.window.transcriber.stop()
        p.history.close()


def test_everything_is_created_and_connected(parts_factory):
    p = parts_factory()
    assert p.window.controller is p.controller and p.window.overlay is p.overlay
    assert p.window.privacy_button.isEnabled() and p.window.settings_button.isEnabled()
    assert p.window.history_button.isEnabled() and p.window.overlay_button.isEnabled()
    assert p.window.audio_panel.before_start is not None  # the consent gate is in the path
    assert p.stt_name == "fake-stt" and p.providers == {}
    assert p.tray.presentation_action.isEnabled() and not p.presentation.active
    assert p.jev.config.redact is True and p.tracker.budget is None


def test_speechmatics_composition_uses_live_workers_without_local_previews(parts_factory):
    from interview_assistant.stt.realtime import RealtimeTranscriber
    from interview_assistant.stt.speechmatics import SpeechmaticsProvider

    stt = SpeechmaticsProvider("dummy", FakeSTT())
    p = parts_factory(stt=stt, stt_provider="speechmatics", speechmatics_api_key="dummy")
    assert isinstance(p.window.transcriber, RealtimeTranscriber)
    assert isinstance(p.window.mic_transcriber, RealtimeTranscriber)
    assert p.window._preview_provider is None
    assert p.window._mic_preview_provider is None
    assert p.window.audio_panel.realtime and p.window.mic_panel.realtime
    assert not p.window.audio_panel.recording and not p.window.mic_panel.recording
    p.window.mic_transcriber.stop()


def test_typed_question_reaches_jev_and_reports_a_missing_provider(qtbot, parts_factory):
    p = parts_factory()
    p.overlay.input.setText("Write a function to solve the two sum problem.")
    p.overlay.ask_btn.click()
    qtbot.waitUntil(lambda: "API key" in p.overlay.answer.toPlainText(), timeout=5000)
    qtbot.waitUntil(lambda: p.history.counts()[1] == 1, timeout=5000)
    (turn,) = p.history.get_turns(p.history.list_sessions()[0].id)
    assert turn.status == "error" and turn.source == "typed"


@pytest.mark.parametrize(
    "sample_index, wording", [(0, None), (1, None), (3, None), (0, "How many catalogs u have sed?")]
)
def test_saved_practice_question_returns_verbatim_without_provider(
    qtbot, parts_factory, monkeypatch, sample_index, wording
):
    from interview_assistant.app.practice_library import EXAMPLES
    from tests.test_router import Script

    marker = "PROVIDER TEST RESPONSE — not the saved practice answer."
    requests = []

    class Spy(Script):
        async def stream(self, request):
            requests.append(request)
            async for event in super().stream(request):
                yield event

    provider = Spy("openai", [marker])
    monkeypatch.setattr(
        "interview_assistant.app.composition.build_providers", lambda s: {"openai": provider}
    )
    p = parts_factory(router_fast="openai:test-model", router_smart="openai:test-model")
    page = p.start_screen._workspace_widgets["practice"]
    page.library.add_examples()
    question, saved_answer = EXAMPLES[sample_index]
    assert any(e.question == question and e.answer == saved_answer for e in page.library.entries())
    p.overlay.input.setText(wording or question)
    with qtbot.waitSignal(p.controller.answer_finished, timeout=10000):
        p.overlay.ask_btn.click()
    assert p.overlay._text == saved_answer
    assert not requests
    assert p.overlay.origin_label.text() == "Saved fictional Q&A"
    assert p.jev.history == []
    p.overlay.prev_qa()
    assert p.overlay.origin_label.text() == "Saved fictional Q&A"
    print(f"Question: {question} | source: saved fictional Q&A | provider calls: 0")


@pytest.mark.parametrize("ambiguous", [False, True])
def test_saved_match_never_requires_selection(qtbot, parts_factory, monkeypatch, ambiguous):
    from tests.test_router import Script

    provider = Script("openai", ["AI fallback test answer"])
    monkeypatch.setattr(
        "interview_assistant.app.composition.build_providers", lambda s: {"openai": provider}
    )
    p = parts_factory(router_fast="openai:test", router_smart="openai:test")
    page = p.start_screen._workspace_widgets["practice"]
    page.library.add_examples()
    if ambiguous:
        page.library.save(
            None, "How many pipelines did you build in your project?", "Different answer"
        )
    p.overlay.input.setText("How many ADF pipelines did you build?")
    with qtbot.waitSignal(p.controller.answer_finished, timeout=5000):
        p.overlay.ask_btn.click()
    if ambiguous:
        assert provider.calls
        assert p.overlay.origin_label.text() == "AI-generated"
        assert "AI fallback test answer" in p.overlay.answer.toPlainText()
    else:
        assert not provider.calls
        assert p.overlay.origin_label.text() == "Saved fictional Q&A"


def test_saved_lookup_is_cancelled_by_presentation(qtbot, parts_factory, monkeypatch):
    import threading

    p = parts_factory()
    p.start_screen._workspace_widgets["practice"].library.add_examples()
    started, release = threading.Event(), threading.Event()
    lookup = p.controller.practice_matcher.lookup

    def delayed(question):
        started.set()
        release.wait(3)
        return lookup(question)

    monkeypatch.setattr(p.controller.practice_matcher, "lookup", delayed)
    try:
        p.controller.ask("How many ADF pipelines did you build?")
        qtbot.waitUntil(started.is_set)
        p.presentation.activate()
    finally:
        release.set()
    qtbot.waitUntil(lambda: not p.controller._runs)
    assert p.overlay.answer.toPlainText() == ""
    assert p.jev.history == []


def test_unrelated_question_uses_ai_with_populated_bank(qtbot, parts_factory, monkeypatch):
    from tests.test_router import Script

    provider = Script("openai", ["AI: lists are mutable; tuples are immutable."])
    monkeypatch.setattr(
        "interview_assistant.app.composition.build_providers", lambda s: {"openai": provider}
    )
    p = parts_factory(router_fast="openai:test", router_smart="openai:test")
    p.start_screen._workspace_widgets["practice"].library.add_examples()
    with qtbot.waitSignal(p.controller.answer_finished, timeout=5000):
        p.controller.ask("What is the difference between a list and a tuple?")
    assert len(provider.calls) == 1  # answer only: no pre-answer classification request
    assert p.overlay.origin_label.text() == "AI-generated"
    assert p.overlay._text.startswith("```text") and p.overlay._text.endswith("```")


def test_local_answer_preserves_literal_text_and_incognito(qtbot, parts_factory):
    p = parts_factory()
    page = p.start_screen._workspace_widgets["practice"]
    answer = "Question: literal text\n\n**Not markdown** <b>literal</b>"
    page.library.save(None, "What was your team size?", answer)
    p.privacy.set_incognito(True)
    with qtbot.waitSignal(p.controller.answer_finished, timeout=5000):
        p.controller.ask("What was your team size?")
    assert p.overlay.answer.toPlainText() == answer
    assert p.history.counts()[1] == 0
    assert not p.jev.history
    p.overlay.prev_qa()
    assert p.overlay.answer.toPlainText() == answer


def test_fix_format_is_local_and_copy_uses_fixed_text(parts_factory):
    p = parts_factory()
    p.overlay.on_question(1, "Example?", "typed")
    p.overlay.on_delta(1, "One paragraph.\n\nAnother paragraph.")
    p.overlay.on_finished(1, "test:provider", 0.1, 0.2)
    p.overlay.input.setText("fix format")
    p.overlay.ask_btn.click()
    assert p.overlay._text == "```text\nOne paragraph.\n```\n\n```text\nAnother paragraph.\n```"
    assert p.overlay.chip.text() == "Format fixed locally"
    assert not p.controller._runs


def test_consecutive_saved_questions_need_no_selection(qtbot, parts_factory):
    p = parts_factory()
    p.start_screen._workspace_widgets["practice"].library.add_examples()
    with qtbot.waitSignal(p.controller.answer_finished, timeout=5000):
        p.controller.ask("How many ADF pipelines did you build?")
    with qtbot.waitSignal(p.controller.answer_finished, timeout=5000):
        p.controller.ask("What was your team size?")
    assert "There were six of us" in p.overlay.answer.toPlainText()


def test_presentation_mode_tray_and_second_launch_behaviour(qtbot, parts_factory):
    p = parts_factory()
    p.presentation.toggle()
    assert not p.overlay.isVisible() and p.tray.presentation_action.isChecked()
    assert "PRESENTATION MODE" in p.tray.tray.toolTip()
    assert "PRESENTATION MODE" in p.window.banner.text()
    p.toggle_overlay()  # the hotkey must not pop the overlay up while presenting
    p.on_second_launch()  # nor may a second app launch
    assert not p.overlay.isVisible() and p.start_screen.isMinimized()
    p.presentation.toggle()
    assert p.overlay.isVisible() and not p.tray.presentation_action.isChecked()
    p.toggle_overlay()
    assert not p.overlay.isVisible()
    p.on_second_launch()  # bring back the workspace without opening another window
    assert not p.overlay.isVisible() and p.start_screen.isVisible()
    assert not p.window.isVisible()


def test_incognito_from_tray_updates_everything(parts_factory):
    p = parts_factory()
    p.tray.incognito_action.trigger()
    assert p.privacy.incognito and p.history.suspended and p.tracker.persist is False
    assert "INCOGNITO" in p.tray.tray.toolTip() and "INCOGNITO" in p.window.banner.text()
    p.tray.incognito_action.trigger()
    assert not p.privacy.incognito and not p.history.suspended


def test_settings_change_offers_a_restart_and_quits_when_accepted(
    qtbot, parts_factory, monkeypatch
):
    monkeypatch.setattr(restart, "_requested", False)
    asked, app = [], StubApp()
    p = parts_factory(qapp=app, restart_ok=lambda w: asked.append(w) or True)
    p.settings_dialog.saved.emit(False)
    assert asked == [] and app.quit_calls == 0  # nothing changed: no prompt
    p.settings_dialog.saved.emit(True)
    assert asked == [p.start_screen] and app.quit_calls == 1 and restart.restart_requested()
    monkeypatch.setattr(restart, "_requested", False)


def test_declining_the_restart_keeps_running(qtbot, parts_factory, monkeypatch):
    monkeypatch.setattr(restart, "_requested", False)
    app = StubApp()
    p = parts_factory(qapp=app, restart_ok=lambda w: False)
    p.settings_dialog.saved.emit(True)
    assert app.quit_calls == 0 and not restart.restart_requested()


def test_capture_is_blocked_until_the_recording_notice_is_accepted(qtbot, parts_factory):
    prompts = []
    p = parts_factory(consent=lambda text: prompts.append(text) or False)
    panel = p.window.audio_panel
    if not panel.toggle_button.isEnabled():
        pytest.skip("no audio devices on this machine")
    panel.toggle_button.click()
    assert not panel.recording and len(prompts) == 1 and "responsible" in prompts[0]


def test_settings_flow_into_the_running_components(parts_factory, tmp_path):
    p = parts_factory(
        budget_usd=4.0,
        budget_scope="day",
        budget_hard_stop=True,
        redact_pii=False,
        incognito=True,
        candidate_profile="Data engineer",
        hotkey_presentation="ctrl+alt+p",
        hotkey_overlay="",
    )
    assert (
        p.tracker.budget and p.tracker.budget.limit_usd == 4.0 and p.tracker.budget.scope == "day"
    )
    assert p.tracker.budget.hard_stop is True
    assert p.jev.config.redact is False and p.jev.config.profile == "Data engineer"
    assert p.privacy.incognito and p.history.suspended
    labels = " ".join(lbl.text() for lbl in p.privacy_dialog.findChildren(QLabel))
    assert "ctrl+alt+p = presentation mode" in labels  # only the configured hotkeys are advertised
    assert "show/hide overlay" not in labels  # hotkey_overlay was disabled ("")


def test_hotkey_bubble_is_advertised_and_can_be_disabled(parts_factory):
    p = parts_factory(hotkey_bubble="ctrl+h")
    labels = " ".join(lbl.text() for lbl in p.privacy_dialog.findChildren(QLabel))
    assert "ctrl+h = collapse overlay to a bubble" in labels
    p2 = parts_factory(hotkey_bubble="")
    labels2 = " ".join(lbl.text() for lbl in p2.privacy_dialog.findChildren(QLabel))
    assert "collapse overlay to a bubble" not in labels2


def test_billed_speech_recognition_uses_local_previews(parts_factory):
    free = parts_factory()
    billed = parts_factory(stt=BilledSTT())
    assert free.window._partials is True and billed.window._partials is True
    assert free.window.transcriber._preview is None
    assert billed.window.transcriber._preview is not None
    assert billed.window.mic_transcriber._preview is not None


@pytest.mark.parametrize("source", ["mic", "loopback"])
@pytest.mark.parametrize("mode", ["manual", "auto"])
def test_live_cloud_preview_updates_overlay_without_answering(qtbot, parts_factory, source, mode):
    from tests.test_audio import tone

    class CloudFinal(BilledSTT):
        def __init__(self):
            self.calls = 0

        def transcribe(self, pcm):
            self.calls += 1
            return "How do you optimise SQL queries?"

    class Preview(FakeSTT):
        def transcribe(self, pcm):
            return "How do you" if len(pcm) < 32000 else "How do you optimise"

    cloud = CloudFinal()
    p = parts_factory(stt=cloud, answer_mode=mode)
    transcriber = p.window.mic_transcriber if source == "mic" else p.window.transcriber
    transcriber._preview._provider = Preview()
    live, pending = [], []
    p.controller.live_text.connect(live.append)
    p.controller.question_pending.connect(lambda text, lag: pending.append(text))
    p.controller.on_speech_started(source)
    transcriber.speech_started()
    transcriber.submit_partial(tone(1))
    qtbot.waitUntil(lambda: "How do you" in live)
    transcriber.submit_partial(tone(2))
    qtbot.waitUntil(lambda: "How do you optimise" in live)
    assert cloud.calls == 0 and p.history.counts()[1] == 0 and pending == []
    assert not p.overlay._busy
    p.controller.on_speech_ended(source)
    transcriber.submit_final(tone(3))
    qtbot.waitUntil(lambda: "How do you optimise SQL queries?" in live)
    assert cloud.calls == 1
    if mode == "manual":
        qtbot.waitUntil(lambda: bool(pending))
        assert p.history.counts()[1] == 0
    else:
        qtbot.waitUntil(lambda: p.history.counts()[1] == 1)


def test_cloud_final_mic_and_system_transcripts_are_visible(parts_factory):
    from interview_assistant.audio.types import DeviceKind
    from interview_assistant.stt.base import TranscriptEvent

    p = parts_factory(stt=BilledSTT(), answer_mode="auto")
    p.window.transcript_panel.event_received.emit(
        TranscriptEvent(1, "What is CDC", True, 1, DeviceKind.LOOPBACK)
    )
    p.window.mic_transcript_received.emit(
        TranscriptEvent(1, "Explain ADF", True, 1, DeviceKind.MIC)
    )
    assert p.window.transcript_panel.final_texts == ["What is CDC", "Explain ADF"]
    assert "Explain ADF" in p.overlay.transcript.text()
    p.window.mic_transcript_received.emit(TranscriptEvent(2, "", True, 1, DeviceKind.MIC))
    assert "No words recognized" in p.overlay.transcript.toolTip()
    assert p.overlay.status_label.text() == "No words"
    p.window.mic_transcript_received.emit(TranscriptEvent(3, "Define", False, 0.5, DeviceKind.MIC))
    assert p.overlay.status_label.text() != "No words"
    assert p.overlay.transcript.toolTip() == "Define"
    p.window.mic_transcript_received.emit(TranscriptEvent(3, "Define RAG", True, 1, DeviceKind.MIC))
    assert "Define RAG" in p.overlay.transcript.text()


def test_old_history_is_purged_at_startup(qtbot, tmp_path, monkeypatch):
    from interview_assistant.cost.factory import data_dir

    settings = Settings(history_retention_days=30, usage_log=False)
    db = data_dir(settings) / "history.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    old = HistoryStore(db, clock=lambda: time.time() - 90 * 86400)
    old.add_turn(TurnRecord("ancient question"))
    old.close()
    monkeypatch.setattr("interview_assistant.app.composition.build_provider", lambda s: FakeSTT())
    ini = QSettings(str(tmp_path / "a.ini"), QSettings.Format.IniFormat)
    parts = build_app(
        StubApp(),
        settings,
        MemoryKeyStore(),
        ini,
        lambda t: True,
        lambda w: True,
        UserSettingsStore(tmp_path / "s.json"),
    )
    try:
        assert parts.history.counts() == (0, 0)
    finally:
        parts.controller.shutdown()
        if parts.window.transcriber:
            parts.window.transcriber.stop()
        parts.history.close()
        qtbot.addWidget(parts.window)
        qtbot.addWidget(parts.overlay)


def test_interview_session_manager_is_wired_into_the_history_window(parts_factory):
    """Step 18: the session dashboard's "+ Create Session" button needs the same
    InterviewSessionManager instance build_app() constructs - not a second, disconnected one."""
    p = parts_factory()
    hw = p.window.history_window
    assert hw is not None
    assert hw._session_manager is p.interview_sessions
    assert hw.create_session_btn.isEnabled()


def test_dual_audio_capture_off_wires_a_single_unrestricted_panel(parts_factory):
    p = parts_factory(dual_audio_capture=False)
    assert p.window.mic_panel is None
    assert p.window.audio_panel._restrict_kind is None  # free to pick any device, as always
    assert p.window.mic_transcriber is None


def test_dual_audio_capture_on_wires_two_fixed_role_panels(parts_factory):
    from interview_assistant.audio.types import DeviceKind

    p = parts_factory(dual_audio_capture=True)
    assert p.window.mic_panel is not None
    assert p.window.audio_panel._restrict_kind is DeviceKind.LOOPBACK
    assert p.window.mic_panel._restrict_kind is DeviceKind.MIC
    assert p.window.transcriber is not None and p.window.mic_transcriber is not None


def test_presentation_mode_stops_both_captures_when_dual_audio_is_on(parts_factory):
    p = parts_factory(dual_audio_capture=True)
    if not p.window.audio_panel.toggle_button.isEnabled():
        pytest.skip("no audio devices on this machine")
    p.window.audio_panel.toggle_button.click()
    if p.window.mic_panel.toggle_button.isEnabled():
        p.window.mic_panel.toggle_button.click()
    p.presentation.activate()
    assert not p.window.audio_panel.recording
    assert not p.window.mic_panel.recording


def test_main_module_imports_and_exposes_entry_point():
    from interview_assistant import __main__ as entry

    assert callable(entry.main) and Path(entry.__file__).name == "__main__.py"


def test_screenshot_asks_for_its_own_consent_notice_not_the_recording_one(parts_factory):
    from interview_assistant.privacy.disclosure import CONSENT_TEXT, SCREENSHOT_CONSENT_TEXT

    prompts = []
    p = parts_factory(consent=lambda text: prompts.append(text) or False)
    assert p.window.overlay.before_screenshot is not None
    assert p.window.overlay.before_screenshot() is False
    assert prompts == [SCREENSHOT_CONSENT_TEXT] and CONSENT_TEXT not in prompts


def test_overlay_menu_carries_the_actions_of_the_old_main_window(parts_factory):
    p = parts_factory()
    labels = [label for label, _ in p.window.overlay._menu_actions]
    assert labels == ["History", "Settings", "Privacy", "New interview", "Audio and transcript"]


def test_session_choices_drive_answer_mode_and_saving_until_the_session_ends(parts_factory):
    from interview_assistant.app.interview_session import SessionDetails

    p = parts_factory()
    assert p.controller._answer_mode == "manual" and not p.privacy.incognito
    p.interview_sessions.activate(
        SessionDetails(company="Accenture", auto_generate=True, save_transcript=False)
    )
    assert p.controller._answer_mode == "auto" and p.privacy.incognito
    p.interview_sessions.end_active()
    assert p.controller._answer_mode == "manual" and not p.privacy.incognito


def test_end_saves_the_session_and_closes_the_app(parts_factory):
    from interview_assistant.app.interview_session import SessionDetails

    app = StubApp()
    p = parts_factory(qapp=app)
    p.interview_sessions.activate(SessionDetails(company="Accenture"))
    p.overlay.close_btn.click()
    assert app.quit_calls == 1
    assert p.interview_sessions.current is None
    (session,) = p.history.list_interview_sessions()
    assert session.ended_at is not None
