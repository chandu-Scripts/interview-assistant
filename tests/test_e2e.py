"""End-to-end scenarios over the REAL pipeline, silently: synthetic audio -> VAD -> speech-to-text
(scripted) -> question merging -> Jev -> router -> overlay / history / cost / privacy.

Only the edges are faked: the microphone (FakeSource), the recognizer (ScriptedSTT) and the AI
providers (scripted streams). Everything in between is production code."""

import time
from dataclasses import dataclass

import numpy as np
import pytest
from PySide6.QtCore import QSettings

from interview_assistant.agents.jev import Jev, JevConfig
from interview_assistant.agents.types import Category
from interview_assistant.app.aggregator import UtteranceAggregator
from interview_assistant.app.controller import AssistantController
from interview_assistant.app.interview_session import InterviewSessionManager, SessionDetails
from interview_assistant.app.presentation import PresentationMode
from interview_assistant.audio.capture import AudioCapture
from interview_assistant.audio.types import DeviceKind
from interview_assistant.cost.pricing import PriceBook
from interview_assistant.cost.tracker import Budget, CostTracker, UsageStore
from interview_assistant.history.store import HistoryStore
from interview_assistant.llm.router import Route, Router, RouterConfig
from interview_assistant.llm.types import ErrorKind, LLMError
from interview_assistant.privacy.state import PrivacyState
from interview_assistant.ui.audio_panel import AudioPanel
from interview_assistant.ui.main_window import MainWindow
from interview_assistant.ui.overlay import OverlayWindow
from tests.test_audio import DEVS, silence, tone
from tests.test_hardening import MortalSource
from tests.test_router import Script

TWO_SUM = "Write a function to solve the two sum problem."
CONFLICT = "Tell me about a time you resolved a conflict on your team."


class ScriptedSTT:
    """Returns the next scripted sentence per finished utterance (no partials in these tests)."""

    name = "scripted"

    def __init__(self, texts):
        self.texts = list(texts)
        self.calls = 0

    def warmup(self):
        pass

    def transcribe(self, pcm):
        self.calls += 1
        return self.texts.pop(0) if self.texts else ""


class Spy(Script):
    """A scripted provider that also records what it was asked."""

    def __init__(self, name, steps, model="m"):
        super().__init__(name, steps, model)
        self.requests = []

    async def stream(self, request):
        self.requests.append(request)
        async for e in super().stream(request):
            yield e


@dataclass
class Stack:
    src: MortalSource
    panel: AudioPanel
    win: MainWindow
    overlay: OverlayWindow
    controller: AssistantController
    tracker: CostTracker
    history: HistoryStore
    privacy: PrivacyState
    presentation: PresentationMode
    stt: ScriptedSTT
    jev: Jev
    usage_path: object
    qtbot: object

    def speak(self, seconds=1.0):
        """One utterance of speech followed by a pause, as the microphone would deliver it."""
        self.src.push(np.concatenate([silence(0.3), tone(seconds), silence(1.0)]))

    def answered(self, text):
        self.qtbot.waitUntil(lambda: text in self.overlay.answer.toPlainText(), timeout=8000)

    def turns(self):
        sessions = self.history.list_sessions()
        return [t for s in reversed(sessions) for t in self.history.get_turns(s.id)]

    def wait_turns(self, n):
        self.qtbot.waitUntil(lambda: len(self.turns()) >= n, timeout=8000)
        return self.turns()

    def start_capture(self):
        self.panel.toggle_button.click()
        assert self.panel.recording


@pytest.fixture
def make_stack(qtbot, tmp_path):
    built: list[Stack] = []

    def make(
        providers,
        stt_texts=(),
        budget=None,
        redact=False,
        chain=None,
        stt=None,
        router_builder=None,
        answer_mode="auto",  # most of these tests exercise the real pipeline end to end; the
        # manual-trigger gate (answer_mode="manual") gets its own dedicated tests below.
    ):
        src = MortalSource()
        factory = lambda d, **kw: AudioCapture(src, d.channels, d.sample_rate, **kw)  # noqa: E731
        panel = AudioPanel(DEVS, factory)
        usage_path = tmp_path / "usage.jsonl"
        tracker = CostTracker(PriceBook(), UsageStore(usage_path), budget)
        chain = chain or [Route(name, p.default_model) for name, p in providers.items()]
        if router_builder is not None:  # real providers (live tests)
            router = router_builder(tracker.record_attempt)
        else:
            router = Router(
                providers,
                RouterConfig(chains={"fast": chain, "smart": chain}),
                tracker.record_attempt,
            )
        jev = Jev(router, JevConfig(profile="Backend engineer", redact=redact))
        history = HistoryStore(tmp_path / "history.db")
        controller = AssistantController(
            jev,
            aggregator=UtteranceAggregator(quiet_s=0.15),
            poll_ms=10,
            tracker=tracker,
            history=history,
            answer_mode=answer_mode,
        )
        tracker.on_change = controller.notify_cost_changed
        overlay = OverlayWindow(
            controller, QSettings(str(tmp_path / "overlay.ini"), QSettings.Format.IniFormat)
        )
        stt = stt or ScriptedSTT(stt_texts)
        win = MainWindow(panel, stt, controller, overlay, partials=False, history=history)
        privacy = PrivacyState(history, tracker, jev.config, redact=redact)
        presentation = PresentationMode([overlay], win, panel, controller)
        for w in (win, overlay):
            qtbot.addWidget(w)
            w.show()
        stack = Stack(
            src, panel, win, overlay, controller, tracker, history, privacy, presentation,
            stt, jev, usage_path, qtbot,
        )  # fmt: skip
        stack.start_capture()
        built.append(stack)
        return stack

    yield make
    for s in built:
        s.panel.stop()
        s.controller.shutdown()
        if s.win.transcriber:
            s.win.transcriber.stop()
        s.history.close()


def hash_provider(name="openai", model="gpt-4o", steps=("Use a ", "hash map.")):
    return Spy(name, list(steps), model)


# ---------------- the main flow ----------------
def test_spoken_question_flows_to_answer_history_and_cost(make_stack):
    s = make_stack({"openai": hash_provider()}, [TWO_SUM])
    s.speak()
    s.answered("hash map.")
    assert s.win.transcript_panel.final_texts == [TWO_SUM]
    (turn,) = s.wait_turns(1)
    assert (turn.question, turn.answer) == (TWO_SUM, "Use a hash map.")
    assert (turn.source, turn.status, turn.category) == ("voice", "complete", "coding")
    assert turn.route == "openai:gpt-4o" and turn.cost_usd and turn.cost_usd > 0
    assert turn.decision_source == "rules"  # classified without an extra model call
    assert s.tracker.summary().calls == 1
    assert "openai:gpt-4o" in s.overlay.chip.text() and "$" in s.overlay.chip.text()
    assert "transcribe" in s.overlay.chip.toolTip()  # the latency breakdown is available
    assert s.overlay.cost_label.text().startswith("$")


def test_a_question_split_by_a_pause_is_answered_once(make_stack):
    prov = hash_provider(steps=("Situation... ", "Result."))
    s = make_stack({"openai": prov}, [CONFLICT, "What was the outcome?"])
    s.speak()
    s.speak()
    s.answered("Result.")
    (turn,) = s.wait_turns(1)
    assert turn.question == f"{CONFLICT} What was the outcome?"
    assert len(prov.requests) == 1  # one merged question -> exactly one model call


def test_manual_mode_waits_for_answer_button_then_streams(make_stack, qtbot):
    s = make_stack({"openai": hash_provider()}, [TWO_SUM], answer_mode="manual")
    s.speak()
    qtbot.waitUntil(lambda: s.overlay.answer_btn.isEnabled(), timeout=8000)
    assert s.overlay.answer.toPlainText() == ""  # not answered yet - just heard and pending
    assert s.turns() == []
    s.controller.trigger_answer()
    s.answered("hash map.")
    (turn,) = s.wait_turns(1)
    assert (turn.question, turn.source) == (TWO_SUM, "voice")


def test_creating_and_activating_an_interview_session_flows_into_the_prompt(make_stack):
    """End-to-end through the real pipeline: activating a session (Step 17) mutates the live
    Jev config, and the very next real request carries its job description/instructions/answer
    preferences - and tags the saved turn's history with the session via the DB link."""
    prov = hash_provider()
    s = make_stack({"openai": prov}, [TWO_SUM])
    mgr = InterviewSessionManager(s.jev.config, s.history, base_profile="Backend engineer")
    session = mgr.activate(
        SessionDetails(
            company="Google",
            role_title="Software Engineer",
            job_description="Build search infrastructure.",
            ai_instructions="Always mention time complexity.",
            answer_format="Bullet points",
        )
    )

    s.speak()
    s.answered("hash map.")
    sent = prov.requests[-1].system
    assert "Google" in sent and "Build search infrastructure." in sent
    assert "Always mention time complexity." in sent and "Bullet points" in sent

    (turn,) = s.wait_turns(1)
    row = s.history._db.execute(
        "SELECT interview_session_id FROM sessions WHERE id = ?", (turn.session_id,)
    ).fetchone()
    assert row[0] == session.id


def test_filler_is_ignored_and_never_saved(make_stack):
    prov = hash_provider()
    s = make_stack({"openai": prov}, ["Okay, great."])
    ignored = []
    s.controller.question_ignored.connect(ignored.append)
    s.speak()
    s.qtbot.waitUntil(lambda: ignored == ["Okay, great."], timeout=8000)
    assert prov.requests == [] and s.history.counts() == (0, 0)
    assert s.overlay.answer_text == ""


def test_wizard_connect_selected_model_end_and_view_transcript(make_stack, qtbot):
    from interview_assistant.ui.history_window import HistoryWindow
    from interview_assistant.ui.start_screen import StartScreen

    default = hash_provider("openai", "default")
    selected = hash_provider("anthropic", "selected")
    s = make_stack({"openai": default, "anthropic": selected}, [TWO_SUM], answer_mode="manual")
    s.panel.stop()
    manager = InterviewSessionManager(s.jev.config, s.history, base_profile="Backend engineer")
    screen = StartScreen(
        s.history,
        None,
        manager.activate,
        s.start_capture,
        lambda: None,
        model_choices=["openai:default", "anthropic:selected"],
        on_resume=manager.resume,
    )
    qtbot.addWidget(screen)
    screen.create_btn.click()
    wizard = screen.create_page
    wizard.company.setText("Northwind")
    wizard.role_title.setText("Platform Architect")
    wizard.next_btn.click()
    wizard.model_override.setCurrentIndex(wizard.model_override.findData("anthropic:selected"))
    wizard.next_btn.click()
    session = manager.current
    assert session is not None and not s.panel.recording
    assert len(s.history.list_interview_sessions()) == 1
    screen.back_btn.click()
    screen.start_btn.click()
    assert manager.current.id == session.id
    assert len(s.history.list_interview_sessions()) == 1
    screen.connect_btn.click()
    assert s.panel.recording
    s.speak()
    qtbot.waitUntil(s.overlay.answer_btn.isEnabled, timeout=8000)
    s.overlay.answer_btn.click()
    s.answered("hash map.")
    s.wait_turns(1)
    assert selected.calls == ["selected"] and default.calls == []
    assert "Role: Platform Architect" in selected.requests[0].system

    def end():
        s.panel.stop()
        manager.end_active()

    s.overlay.end_requested.connect(end)
    s.overlay.close_btn.click()
    assert not s.panel.recording
    assert s.history.get_interview_session(session.id).ended_at is not None
    assert s.jev.config.model_route_override == ""
    history = HistoryWindow(s.history)
    qtbot.addWidget(history)
    history.select_interview_session(session.id)
    assert TWO_SUM in history.detail.toPlainText()
    assert "hash map." in history.detail.toPlainText()
    s.controller.ask(TWO_SUM, "typed")
    s.wait_turns(2)
    assert default.calls == ["default"] and selected.calls == ["selected"]


def test_consecutive_questions_keep_context(make_stack):
    prov = hash_provider(steps=("ok",))
    s = make_stack({"openai": prov}, [CONFLICT, "What was the outcome of that?"])
    s.speak()
    s.wait_turns(1)
    s.speak()
    s.wait_turns(2)
    second = prov.requests[1]
    assert [m.role for m in second.messages] == ["user", "assistant", "user"]
    assert second.messages[0].content == CONFLICT
    t1, t2 = s.turns()
    assert t2.category == t1.category  # the follow-up stayed with the same specialist


def test_correcting_the_specialist_redoes_and_records_the_correction(make_stack):
    prov = hash_provider()
    s = make_stack({"openai": prov}, [TWO_SUM])
    corrected = []
    s.controller.category_corrected.connect(lambda q, o, n: corrected.append((q, o, n)))
    s.speak()
    s.answered("hash map.")
    (first,) = s.wait_turns(1)
    assert first.category == "coding" and first.corrected_from == ""
    s.controller.correct_category(Category.HR)
    s.qtbot.waitUntil(lambda: len(prov.requests) == 2, timeout=8000)
    s.wait_turns(2)
    first, second = s.turns()
    assert corrected == [(TWO_SUM, "coding", "hr")]
    assert second.category == "hr" and second.corrected_from == "coding"
    assert second.decision_source == "user"
    assert "hr" in s.overlay.chip.text()
    assert prov.requests[1].system != prov.requests[0].system  # HR's prompt, not coding's


def test_claim_prone_answer_triggers_the_verify_warning(make_stack):
    prov = hash_provider(steps=("We drove a ", "20% increase in throughput."))
    s = make_stack({"openai": prov}, [CONFLICT])  # classifies as behavioral (claim-prone)
    s.speak()
    s.answered("20% increase")
    (turn,) = s.wait_turns(1)
    assert turn.category == "behavioral"
    assert "20%" in s.overlay.warn_label.text()


# ---------------- failures ----------------
def test_provider_failure_mid_answer_recovers_on_another_provider(make_stack):
    bad = Spy("openai", ["par", "tial", LLMError("openai", ErrorKind.SERVER, "boom")], "gpt-4o")
    good = Spy("gemini", ["clean answer"], "gemini-3.1-flash-lite")
    s = make_stack({"openai": bad, "gemini": good}, [TWO_SUM])
    s.speak()
    s.answered("clean answer")
    assert "partial" not in s.overlay.answer.toPlainText()  # the failed attempt was discarded
    (turn,) = s.wait_turns(1)
    assert turn.answer == "clean answer" and turn.route == "gemini:gemini-3.1-flash-lite"
    summary = s.tracker.summary()
    assert summary.calls == 2 and summary.wasted_usd > 0  # the aborted attempt still cost tokens


def test_all_providers_down_shows_a_clear_error(make_stack):
    p1 = Spy("openai", [LLMError("openai", ErrorKind.AUTH, "bad key")], "gpt-4o")
    p2 = Spy("gemini", [LLMError("gemini", ErrorKind.SERVER, "down")], "gemini-3.1-flash-lite")
    s = make_stack({"openai": p1, "gemini": p2}, [TWO_SUM])
    s.speak()
    s.qtbot.waitUntil(
        lambda: "All AI providers failed" in s.overlay.answer.toPlainText(), timeout=8000
    )
    (turn,) = s.wait_turns(1)
    assert turn.status == "error" and "openai" in turn.error
    s.panel.check_health()
    assert s.panel.recording  # a model outage does not stop listening


def test_no_provider_configured_explains_what_to_do(make_stack):
    s = make_stack({}, [TWO_SUM], chain=[Route("openai", "gpt-4o")])
    s.speak()
    s.qtbot.waitUntil(lambda: "API key" in s.overlay.answer.toPlainText(), timeout=8000)


def test_capture_device_dies_and_recovers(make_stack):
    s = make_stack({"openai": hash_provider()}, [TWO_SUM, TWO_SUM])
    s.speak()
    s.answered("hash map.")
    s.src.alive_flag = False
    s.panel.check_health()
    assert not s.panel.recording and "stopped" in s.panel.error_label.text()
    s.src.alive_flag = True
    s.start_capture()  # the user presses Start again
    s.speak()
    s.wait_turns(2)


def test_interrupting_a_running_answer_keeps_the_partial_and_answers_the_new_one(make_stack):
    slow = Spy("openai", ["a", ("sleep", 1.0), "b", ("sleep", 1.0), "c"], "gpt-4o")
    s = make_stack({"openai": slow}, [TWO_SUM, "Explain how garbage collection works?"])
    s.speak()
    s.qtbot.waitUntil(lambda: s.overlay.answer_text.startswith("a"), timeout=8000)
    slow.steps = ["fresh answer"]
    s.speak()
    s.answered("fresh answer")
    first, second = s.wait_turns(2)
    assert first.status == "interrupted" and first.answer.startswith("a")
    assert second.status == "complete" and second.answer == "fresh answer"


# ---------------- privacy ----------------
def test_incognito_saves_nothing_then_resumes(make_stack):
    s = make_stack({"openai": hash_provider()}, [TWO_SUM, TWO_SUM])
    s.privacy.set_incognito(True)
    s.speak()
    s.answered("hash map.")
    s.qtbot.wait(300)
    assert s.history.counts() == (0, 0) and not s.usage_path.exists()
    assert s.tracker.summary().calls == 1  # still tracked in memory for the on-screen cost
    s.privacy.set_incognito(False)
    s.speak()
    s.wait_turns(1)
    assert s.history.counts() == (1, 1)


def test_redaction_masks_what_is_sent_but_history_keeps_the_original(make_stack):
    prov = hash_provider()
    spoken = "Explain how OAuth works? My email is bob@example.com"
    s = make_stack({"openai": prov}, [spoken], redact=True)
    s.speak()
    (turn,) = s.wait_turns(1)
    sent = " ".join(m.content for m in prov.requests[0].messages) + (prov.requests[0].system or "")
    assert "bob@example.com" not in sent and "[EMAIL]" in sent
    assert "bob@example.com" in turn.question  # the local copy is untouched


def test_presentation_mode_hides_pauses_and_never_auto_resumes(make_stack):
    prov = hash_provider()
    s = make_stack({"openai": prov}, [TWO_SUM, TWO_SUM, TWO_SUM])
    s.speak()
    s.wait_turns(1)
    s.presentation.activate()
    assert not s.overlay.isVisible() and s.win.isMinimized() and not s.panel.recording
    assert s.controller.paused
    s.controller.ask("Typed while presenting?")  # ignored
    s.qtbot.wait(300)
    assert len(prov.requests) == 1 and s.history.counts()[1] == 1
    s.presentation.deactivate()
    assert s.overlay.isVisible() and not s.panel.recording  # windows back, capture still off
    s.start_capture()
    s.speak()
    s.wait_turns(2)


def test_hard_budget_stops_answers_and_says_why(make_stack):
    s = make_stack(
        {"openai": hash_provider()}, [TWO_SUM, TWO_SUM], budget=Budget(1e-7, hard_stop=True)
    )
    s.speak()
    s.wait_turns(1)
    s.qtbot.waitUntil(lambda: s.tracker.budget_state().blocked, timeout=5000)
    s.speak()
    s.qtbot.waitUntil(lambda: "Budget reached" in s.overlay.answer.toPlainText(), timeout=8000)
    assert s.wait_turns(2)[1].status == "error"
    assert len(s.jev._router._providers["openai"].requests) == 1  # the 2nd never reached a model


def test_new_interview_resets_context_history_session_and_cost(make_stack):
    prov = hash_provider(steps=("ok",))
    s = make_stack({"openai": prov}, [CONFLICT, CONFLICT])
    s.speak()
    s.wait_turns(1)
    s.win.clear_button.click()
    assert s.tracker.summary("session").calls == 0
    s.speak()
    s.wait_turns(2)
    assert len(prov.requests[1].messages) == 1  # no memory of the previous interview
    assert s.history.counts()[0] == 2  # two separate history sessions


# ---------------- longer runs ----------------
def test_a_long_session_stays_consistent(make_stack):
    n = 25
    texts = [f"Explain how thing number {i} works in the system?" for i in range(n)]
    s = make_stack({"openai": hash_provider(steps=("ok",))}, texts)
    started = time.time()
    for i in range(n):
        s.speak(0.6)
        s.wait_turns(i + 1)
    assert [t.question for t in s.turns()] == texts  # in order, none lost or duplicated
    assert all(t.status == "complete" for t in s.turns())
    assert s.tracker.summary().calls == n and len(s.jev.history) <= 10
    assert len(s.win.transcript_panel.final_texts) == n
    assert time.time() - started < 60  # no slowdown / hang


def test_everything_shuts_down_cleanly(make_stack):
    s = make_stack({"openai": hash_provider()}, [TWO_SUM])
    s.speak()
    s.wait_turns(1)
    s.win.close()
    assert not s.panel.recording and not s.overlay.isVisible()
    assert s.history.current_session is None  # the session was closed


# ---------------- Step 19: simultaneous mic + system audio capture ----------------
def test_simultaneous_mic_and_loopback_capture_through_the_real_pipeline(qtbot, tmp_path):
    """Two independent FakeSource -> AudioCapture -> VAD -> StreamingTranscriber pipelines (real
    production code throughout, only the microphone/speaker hardware is faked), feeding ONE
    AssistantController/UtteranceAggregator. The two utterances overlap in flight - mic speech
    starts before loopback's has finalized - to exercise the aggregator's per-source bookkeeping
    through real worker threads, not just a direct unit test of UtteranceAggregator itself."""
    loop_src, mic_src = MortalSource(), MortalSource()
    loop_panel = AudioPanel(
        [DEVS[1]],
        lambda d, **kw: AudioCapture(loop_src, d.channels, d.sample_rate, **kw),
        restrict_kind=DeviceKind.LOOPBACK,
    )
    mic_panel = AudioPanel(
        [DEVS[0]],
        lambda d, **kw: AudioCapture(mic_src, d.channels, d.sample_rate, **kw),
        restrict_kind=DeviceKind.MIC,
    )
    loop_stt = ScriptedSTT(["What is the time complexity?"])
    mic_stt = ScriptedSTT(["What is a mutex?"])

    prov = hash_provider()
    chain = [Route("openai", prov.default_model)]
    router = Router({"openai": prov}, RouterConfig(chains={"fast": chain, "smart": chain}))
    jev = Jev(router, JevConfig(profile="Backend engineer"))
    history = HistoryStore(tmp_path / "history.db")
    controller = AssistantController(
        jev,
        aggregator=UtteranceAggregator(quiet_s=0.15),
        poll_ms=10,
        history=history,
        answer_mode="auto",  # exercising the real pipeline end to end, not the manual-trigger gate
    )
    overlay = OverlayWindow(
        controller, QSettings(str(tmp_path / "o.ini"), QSettings.Format.IniFormat)
    )
    win = MainWindow(
        loop_panel,
        loop_stt,
        controller,
        overlay,
        partials=False,
        history=history,
        audio_source="loopback",
        mic_panel=mic_panel,
        mic_provider=mic_stt,
    )
    qtbot.addWidget(win)
    qtbot.addWidget(overlay)
    win.show()
    try:
        loop_panel.toggle_button.click()
        mic_panel.toggle_button.click()
        assert loop_panel.recording and mic_panel.recording

        # Overlapping: loopback speech starts, then mic speech starts BEFORE loopback finalizes.
        loop_src.push(np.concatenate([silence(0.2), tone(0.6)]))
        mic_src.push(np.concatenate([silence(0.2), tone(0.6), silence(1.0)]))
        loop_src.push(silence(1.0))  # loopback's own trailing silence, to finalize it too

        def turns():
            sessions = history.list_sessions()
            return [t for s in reversed(sessions) for t in history.get_turns(s.id)]

        qtbot.waitUntil(lambda: len(turns()) >= 2, timeout=8000)
        by_source = {t.source: t for t in turns()}
        assert by_source["voice:loopback"].question == "What is the time complexity?"
        assert by_source["voice:mic"].question == "What is a mutex?"
        # Neither source's text leaked into the other's.
        assert "mutex" not in by_source["voice:loopback"].question
        assert "time complexity" not in by_source["voice:mic"].question
        assert by_source["voice:loopback"].answer and by_source["voice:mic"].answer
    finally:
        win.close()
        history.close()


def test_screenshot_button_sends_the_capture_with_the_next_question_only(make_stack, monkeypatch):
    """Step 20 through the real stack: overlay button -> controller -> Jev -> provider request."""
    import interview_assistant.ui.overlay as overlay_mod

    shot = b"\x89PNG\r\n\x1a\nscreen"
    monkeypatch.setattr(overlay_mod, "capture_screen_png", lambda: shot)
    prov = hash_provider()
    s = make_stack({"openai": prov}, [])
    s.overlay.screenshot_btn.click()  # answered immediately, no question needed
    s.wait_turns(1)
    s.overlay.input.setText(TWO_SUM)
    s.overlay.ask_btn.click()
    s.wait_turns(2)
    s.overlay.input.setText("Write a function to reverse a linked list.")
    s.overlay.ask_btn.click()
    s.wait_turns(3)
    assert [r.image for r in prov.requests] == [shot, None, None]
