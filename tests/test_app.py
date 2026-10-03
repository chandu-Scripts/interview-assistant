import asyncio
import pathlib

import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication

from interview_assistant.agents.jev import Jev, JevConfig
from interview_assistant.agents.types import Category, Decision
from interview_assistant.app.aggregator import UtteranceAggregator
from interview_assistant.app.async_runner import AsyncRunner
from interview_assistant.app.controller import AssistantController, friendly_error
from interview_assistant.audio.types import DeviceKind
from interview_assistant.history.store import HistoryStore
from interview_assistant.llm.router import (
    AllRoutesFailed,
    NoRoutesAvailable,
    Route,
    RouteEvent,
    Router,
    RouterConfig,
)
from interview_assistant.llm.types import ErrorKind, LLMError, StreamEvent
from interview_assistant.stt.base import TranscriptEvent
from interview_assistant.ui.overlay import OverlayWindow
from tests.test_router import Script


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


# ---------------- aggregator ----------------
def agg(**kw):
    c = Clock()
    return UtteranceAggregator(clock=c, **kw), c


def test_aggregator_single_utterance_released_after_quiet():
    a, c = agg(quiet_s=1.0)
    a.speech_started()
    a.final("Tell me about a conflict.")
    assert a.poll() is None  # too soon
    c.t += 1.1
    assert a.poll() == ("Tell me about a conflict.", "")
    assert a.poll() is None


def test_aggregator_merges_split_question():
    a, c = agg(quiet_s=1.0)
    a.speech_started()
    a.final("Tell me about a time you resolved a conflict.")
    c.t += 0.5
    a.speech_started()  # interviewer continues -> second utterance in flight
    c.t += 2.0
    assert a.poll() is None  # held: still transcribing
    a.final("What was the outcome?")
    c.t += 1.1
    assert a.poll() == (
        "Tell me about a time you resolved a conflict. What was the outcome?",
        "",
    )


def test_aggregator_max_hold_and_stale_in_flight():
    a, c = agg(quiet_s=1.0, max_hold_s=8.0, stale_s=20.0)
    a.speech_started()
    a.final("Part one.")
    a.speech_started()  # never completes (STT error)
    c.t += 7
    assert a.poll() is None
    c.t += 2  # max hold exceeded
    assert a.poll() == ("Part one.", "")
    c.t += 30  # stale start is discarded so it cannot block later questions
    a.poll()
    assert a.in_flight == 0


def test_aggregator_empty_final_and_reset():
    a, c = agg()
    a.speech_started()
    a.final("   ")
    c.t += 5
    assert a.poll() is None and a.in_flight == 0
    a.speech_started()
    a.final("x")
    a.reset()
    c.t += 5
    assert a.poll() is None


def test_aggregator_two_sources_dont_cross_contaminate():
    """Step 19: a mic utterance and a loopback utterance in flight concurrently must merge
    independently - neither source's text/timers can leak into the other's."""
    a, c = agg(quiet_s=1.0)
    a.speech_started("mic")
    a.final("Candidate speaking,", "mic")
    c.t += 0.3
    a.speech_started("loopback")
    a.final("Interviewer: what is a mutex?", "loopback")
    assert a.poll() is None  # both still within their own quiet window
    c.t += 1.1
    # loopback's quiet window closed first (started 0.3s later but nothing extended it);
    # poll() returns sources in the order first seen - "mic" was registered first.
    first = a.poll()
    second = a.poll()
    assert {first, second} == {
        ("Candidate speaking,", "mic"),
        ("Interviewer: what is a mutex?", "loopback"),
    }
    assert a.poll() is None


def test_aggregator_source_in_flight_count_is_independent():
    a, c = agg(quiet_s=1.0, max_hold_s=8.0)
    a.speech_started("mic")
    a.speech_started("loopback")
    a.final("loopback done", "loopback")
    c.t += 1.1
    assert a.poll() == ("loopback done", "loopback")
    assert a.in_flight == 1  # the mic utterance is still in flight, untouched


# ---------------- async runner ----------------
def test_async_runner_runs_and_stops():
    r = AsyncRunner()

    async def work():
        await asyncio.sleep(0.01)
        return 42

    assert r.submit(work()).result(2) == 42
    r.stop()
    r.stop()  # idempotent


# ---------------- controller ----------------
def decision(cat=Category.CODING, ok=True, source="rules"):
    return Decision(cat if ok else Category.NOT_A_QUESTION, "fast", 0.9, source, "x")


class FakeJevConfig:
    def __init__(self, profile=None):
        self.profile = profile


class FakeJev:
    def __init__(self, delays=None, chunks=("Hel", "lo"), fail=None, hold=None, profile=None):
        self.delays, self.chunks, self.fail, self.hold = delays or {}, chunks, fail, hold
        self.asked: list[str] = []
        self.images: list[bytes | None] = []
        self.cleared = 0
        self.config = FakeJevConfig(profile)

    async def decide(self, q):
        await asyncio.sleep(self.delays.get(q, 0))
        return decision(ok=not q.lower().startswith("okay"))

    async def ask(self, q, decision=None, image=None):
        self.asked.append(q)
        self.images.append(image)
        if self.fail:
            raise self.fail
        for c in self.chunks:
            yield RouteEvent(StreamEvent(delta=c), Route("p", "m"), 0)
            if self.hold:
                await asyncio.sleep(self.hold)

    def manual_decision(self, category):
        return decision(cat=category, source="user")

    def clear_history(self):
        self.cleared += 1


class Recorder:
    def __init__(self, c: AssistantController):
        self.log: list[tuple] = []
        c.question_started.connect(lambda g, t, s: self.log.append(("q", g, t, s)))
        c.decision_ready.connect(lambda g, d: self.log.append(("d", g)))
        c.answer_delta.connect(lambda g, t: self.log.append(("delta", g, t)))
        c.answer_reset.connect(lambda g: self.log.append(("reset", g)))
        c.answer_finished.connect(lambda g, r, f, t: self.log.append(("done", g, r)))
        c.answer_failed.connect(lambda g, m: self.log.append(("fail", g, m)))
        c.answer_stopped.connect(lambda g: self.log.append(("stopped", g)))
        c.question_ignored.connect(lambda t: self.log.append(("ignored", t)))
        c.busy_changed.connect(lambda b: self.log.append(("busy", b)))

    def kinds(self):
        return [e[0] for e in self.log]

    def has(self, *e):
        return e in self.log


@pytest.fixture
def make_controller(qtbot):
    made = []

    def make(jev, **kw):
        c = AssistantController(jev, poll_ms=10, **kw)
        made.append(c)
        return c, Recorder(c)

    yield make
    for c in made:
        c.shutdown()


def test_typed_question_streams_in_order(qtbot, make_controller):
    c, rec = make_controller(FakeJev())
    c.ask("What is a mutex?")
    qtbot.waitUntil(lambda: rec.has("busy", False), timeout=3000)
    assert rec.log == [
        ("q", 1, "What is a mutex?", "typed"),
        ("busy", True),
        ("d", 1),
        ("delta", 1, "Hel"),
        ("delta", 1, "lo"),
        ("done", 1, "p:m"),
        ("busy", False),
    ]


def test_voice_filler_is_ignored_and_keeps_current_answer(qtbot, make_controller):
    c, rec = make_controller(FakeJev())
    c.ask("What is a mutex?", "voice")
    qtbot.waitUntil(lambda: rec.has("busy", False), timeout=3000)
    n = len(rec.log)
    c.ask("Okay, great.", "voice")
    qtbot.waitUntil(lambda: rec.has("ignored", "Okay, great."), timeout=3000)
    assert len(rec.log) == n + 1  # nothing else happened: no reset of the answer on screen


def test_new_question_cancels_running_answer(qtbot, make_controller):
    c, rec = make_controller(FakeJev(chunks=("a", "b", "c", "d"), hold=0.8))
    c.ask("First question here?")
    qtbot.waitUntil(lambda: rec.has("delta", 1, "a"), timeout=3000)
    c.ask("Second question here?")
    # Wait for busy_changed(False) specifically, not just "done" - they're two separate queued
    # cross-thread signals emitted back-to-back from the background thread, and waiting only for
    # "done" can let the check pass while "busy, False" is still in flight, so the final assertion
    # below would race it.
    qtbot.waitUntil(lambda: rec.has("busy", False), timeout=15000)
    assert ("done", 1, "p:m") not in rec.log  # first was cancelled mid-stream
    assert rec.has("q", 2, "Second question here?", "typed")
    late = [
        e
        for e in rec.log[rec.log.index(("q", 2, "Second question here?", "typed")) :]
        if e[:2] == ("delta", 1)
    ]
    assert late == []  # no first-answer text after the second question started
    assert rec.log[-1] == ("busy", False)


def test_older_slow_decision_is_dropped(qtbot, make_controller):
    c, rec = make_controller(FakeJev(delays={"slow old question?": 0.4}))
    c.ask("slow old question?")
    c.ask("fast new question?")
    qtbot.waitUntil(lambda: rec.has("done", 2, "p:m"), timeout=3000)
    qtbot.wait(700)
    assert not any(e[0] == "q" and e[1] == 1 for e in rec.log)  # stale one never displayed


def test_stop_cancels_answer(qtbot, make_controller):
    c, rec = make_controller(FakeJev(chunks=("a", "b", "c"), hold=0.5))
    c.ask("Explain how things work?")
    qtbot.waitUntil(lambda: rec.has("delta", 1, "a"), timeout=3000)
    c.stop()
    qtbot.waitUntil(lambda: rec.has("stopped", 1), timeout=3000)
    qtbot.waitUntil(lambda: rec.log[-1] == ("busy", False), timeout=3000)
    assert "done" not in rec.kinds()


def test_error_is_reported_friendly(qtbot, make_controller):
    c, rec = make_controller(FakeJev(fail=NoRoutesAvailable("x")))
    c.ask("What is a mutex?")
    qtbot.waitUntil(lambda: rec.has("busy", False), timeout=3000)
    assert any(e[0] == "fail" and "API key" in e[2] for e in rec.log)


def test_friendly_error_variants():
    assert "API key" in friendly_error(NoRoutesAvailable("x"))
    e = AllRoutesFailed([LLMError("openai", ErrorKind.AUTH, "bad key")])
    assert "openai" in friendly_error(e)
    assert friendly_error(ValueError("boom")).startswith("ValueError")


def test_voice_fragments_merge_into_one_question(qtbot, make_controller):
    jev = FakeJev()
    c, rec = make_controller(jev, aggregator=UtteranceAggregator(quiet_s=0.2), answer_mode="auto")
    c.on_speech_started()
    c.on_transcript_event(TranscriptEvent(1, "Tell me about a time you failed.", True, 3))
    c.on_speech_started()  # second fragment still transcribing
    qtbot.wait(400)
    assert jev.asked == []  # held back
    c.on_transcript_event(TranscriptEvent(2, "What did you learn?", True, 2))
    qtbot.waitUntil(lambda: rec.has("busy", False), timeout=3000)
    assert jev.asked == ["Tell me about a time you failed. What did you learn?"]
    assert rec.has("q", 1, "Tell me about a time you failed. What did you learn?", "voice")


def test_two_simultaneous_audio_sources_merge_and_tag_independently(qtbot, make_controller):
    """Step 19: a mic utterance and a loopback utterance in flight at the same time must not
    merge into each other's text, and each ends up tagged "voice:<source>" once answered. Uses
    an injected clock (like the pure aggregator tests above) so the quiet-window timing is
    deterministic instead of racing real wall-clock time under system load."""
    jev = FakeJev()
    clock = Clock()
    agg_ = UtteranceAggregator(quiet_s=0.15, clock=clock)
    c, rec = make_controller(jev, aggregator=agg_, answer_mode="auto")
    c.on_speech_started(DeviceKind.MIC.value)
    c.on_transcript_event(
        TranscriptEvent(1, "Candidate: I used a hash map.", True, 2, DeviceKind.MIC)
    )
    clock.t += 0.3
    c.on_speech_started(DeviceKind.LOOPBACK.value)
    c.on_transcript_event(
        TranscriptEvent(
            2, "Interviewer: what is the time complexity?", True, 2, DeviceKind.LOOPBACK
        )
    )
    clock.t += 0.3  # past quiet_s for both sources now, regardless of real elapsed time
    # Wait on the Recorder (populated only once its queued cross-thread signals are actually
    # delivered) rather than jev.asked (a plain list the background asyncio thread mutates
    # directly) - otherwise the len(jev.asked) check can observably race ahead of signal
    # delivery and this assertion would read stale state.
    qtbot.waitUntil(
        lambda: (
            rec.has("q", 1, "Candidate: I used a hash map.", "voice:mic")
            and rec.has("q", 2, "Interviewer: what is the time complexity?", "voice:loopback")
        ),
        timeout=3000,
    )
    assert set(jev.asked) == {
        "Candidate: I used a hash map.",
        "Interviewer: what is the time complexity?",
    }


# ---------------- controller: manual-trigger answering (answer_mode) ----------------
def test_manual_mode_buffers_voice_question_until_triggered(qtbot, make_controller):
    jev = FakeJev()
    c, rec = make_controller(jev, aggregator=UtteranceAggregator(quiet_s=0.15))  # default: manual
    pending = []
    c.question_pending.connect(lambda t, lag: pending.append((t, lag)))
    c.on_speech_started()
    c.on_transcript_event(TranscriptEvent(1, "What is a mutex?", True, 2))
    qtbot.waitUntil(lambda: len(pending) > 0, timeout=3000)
    assert pending == [("What is a mutex?", None)]  # no on_speech_ended -> lag is None
    assert jev.asked == []  # not answered yet - waiting for trigger_answer()
    assert not rec.has("q", 1, "What is a mutex?", "voice")
    c.trigger_answer()
    qtbot.waitUntil(lambda: rec.has("busy", False), timeout=3000)
    assert jev.asked == ["What is a mutex?"]
    assert rec.has("q", 1, "What is a mutex?", "voice")


def test_trigger_answer_without_pending_is_a_noop(qtbot, make_controller):
    jev = FakeJev()
    c, rec = make_controller(jev)
    c.trigger_answer()
    qtbot.wait(100)
    assert jev.asked == [] and rec.log == []


def test_manual_mode_later_question_replaces_earlier_pending_one(qtbot, make_controller):
    jev = FakeJev()
    c, rec = make_controller(jev, aggregator=UtteranceAggregator(quiet_s=0.15))
    pending = []
    c.question_pending.connect(lambda t, lag: pending.append(t))
    c.on_speech_started()
    c.on_transcript_event(TranscriptEvent(1, "First question?", True, 2))
    qtbot.waitUntil(lambda: pending == ["First question?"], timeout=3000)
    c.on_speech_started()
    c.on_transcript_event(TranscriptEvent(2, "Second question?", True, 2))
    qtbot.waitUntil(lambda: pending == ["First question?", "Second question?"], timeout=3000)
    c.trigger_answer()
    qtbot.waitUntil(lambda: rec.has("busy", False), timeout=3000)
    assert jev.asked == ["Second question?"]  # the first was superseded, never answered


def test_set_paused_clears_pending_question(qtbot, make_controller):
    jev = FakeJev()
    c, rec = make_controller(jev, aggregator=UtteranceAggregator(quiet_s=0.15))
    c.on_speech_started()
    c.on_transcript_event(TranscriptEvent(1, "What is a mutex?", True, 2))
    qtbot.waitUntil(lambda: c._pending_question is not None, timeout=3000)
    c.set_paused(True)
    c.set_paused(False)
    c.trigger_answer()
    qtbot.wait(100)
    assert jev.asked == []


def test_new_interview_clears_pending_question(qtbot, make_controller):
    jev = FakeJev()
    c, rec = make_controller(jev, aggregator=UtteranceAggregator(quiet_s=0.15))
    c.on_speech_started()
    c.on_transcript_event(TranscriptEvent(1, "What is a mutex?", True, 2))
    qtbot.waitUntil(lambda: c._pending_question is not None, timeout=3000)
    c.new_interview()
    c.trigger_answer()
    qtbot.wait(100)
    assert jev.asked == []


def test_auto_mode_still_answers_immediately(qtbot, make_controller):
    """answer_mode="auto" is the pre-Step-14 behavior, kept for anyone who wants it back."""
    jev = FakeJev()
    c, rec = make_controller(jev, aggregator=UtteranceAggregator(quiet_s=0.15), answer_mode="auto")
    pending = []
    c.question_pending.connect(lambda t, lag: pending.append(t))
    c.on_speech_started()
    c.on_transcript_event(TranscriptEvent(1, "What is a mutex?", True, 2))
    qtbot.waitUntil(lambda: rec.has("busy", False), timeout=3000)
    assert jev.asked == ["What is a mutex?"]
    assert pending == []  # question_pending is only emitted in manual mode


def test_live_partial_text_signal(qtbot, make_controller):
    c, _ = make_controller(FakeJev())
    with qtbot.waitSignal(c.live_text, timeout=1000) as sig:
        c.on_transcript_event(TranscriptEvent(1, "tell me abo", False, 1))
    assert sig.args == ["tell me abo"]


def test_clear_history_delegates(qtbot, make_controller):
    jev = FakeJev()
    c, _ = make_controller(jev)
    c.clear_history()
    assert jev.cleared == 1


# ---------------- controller: "wrong specialist?" correction ----------------
def test_correct_category_redoes_with_new_specialist_and_logs_correction(qtbot, make_controller):
    jev = FakeJev(chunks=("fixed",))
    store = HistoryStore(":memory:")
    c, rec = make_controller(jev, history=store)
    corrected = []
    c.category_corrected.connect(lambda q, o, n: corrected.append((q, o, n)))
    c.ask("Find the second highest salary?")
    qtbot.waitUntil(lambda: rec.has("busy", False), timeout=3000)
    c.correct_category(Category.DATA_SQL)
    # Wait on `corrected` itself (populated only once its own queued cross-thread signal is
    # delivered), not on jev.asked/rec.log[-1] as a proxy for it - those are separate signals/state
    # and waiting on one as a stand-in for another can race its actual delivery.
    qtbot.waitUntil(lambda: len(corrected) == 1, timeout=3000)
    qtbot.waitUntil(lambda: rec.log[-1] == ("busy", False), timeout=3000)
    assert corrected == [("Find the second highest salary?", "coding", "data_sql")]
    turns = store.get_turns(store.list_sessions()[0].id)
    assert [t.category for t in turns] == ["coding", "data_sql"]
    assert [t.decision_source for t in turns] == ["rules", "user"]
    assert turns[1].corrected_from == "coding" and turns[1].answer == "fixed"
    store.close()


def test_correct_category_same_category_is_a_plain_regenerate(qtbot, make_controller):
    jev = FakeJev(chunks=("again",))
    store = HistoryStore(":memory:")
    c, _ = make_controller(jev, history=store)
    corrected = []
    c.category_corrected.connect(lambda q, o, n: corrected.append((q, o, n)))
    c.ask("Write a function to reverse a list.")
    qtbot.waitUntil(lambda: jev.asked == ["Write a function to reverse a list."], timeout=3000)
    c.correct_category(Category.CODING)  # FakeJev.decide always answers CODING
    qtbot.waitUntil(lambda: jev.asked == ["Write a function to reverse a list."] * 2, timeout=3000)
    qtbot.wait(100)
    assert corrected == []  # not logged as a correction: nothing was actually wrong
    turns = store.get_turns(store.list_sessions()[0].id)
    assert turns[1].corrected_from == "" and turns[1].answer == "again"
    store.close()


def test_correct_category_without_a_prior_question_is_a_noop(qtbot, make_controller):
    jev = FakeJev()
    c, rec = make_controller(jev)
    c.correct_category(Category.DATA_SQL)
    qtbot.wait(200)
    assert jev.asked == [] and rec.log == []


def test_correct_category_cancels_a_running_answer(qtbot, make_controller):
    jev = FakeJev(chunks=("a", "b", "c", "d"), hold=0.8)
    c, rec = make_controller(jev)
    c.ask("Find the second highest salary?")
    qtbot.waitUntil(lambda: rec.has("delta", 1, "a"), timeout=3000)
    c.correct_category(Category.DATA_SQL)
    qtbot.waitUntil(lambda: rec.has("done", 2, "p:m"), timeout=15000)
    assert ("done", 1, "p:m") not in rec.log  # the first, wrongly-classified answer was cancelled


# ---------------- controller: "verify before saying" claim guard ----------------
def test_answer_flags_only_for_claim_prone_categories(qtbot, make_controller):
    jev = FakeJev(chunks=("We saw a 20% increase in throughput.",))
    c, _ = make_controller(jev)  # FakeJev classifies everything as CODING (not claim-prone)
    flags = []
    c.answer_flags.connect(lambda g, f: flags.append(f))
    c.ask("Tell me about a time you improved performance.")
    qtbot.waitUntil(lambda: flags == [[]], timeout=3000)


def test_answer_flags_unverified_percentage_for_behavioral(qtbot, make_controller):
    class BehavioralJev(FakeJev):
        async def decide(self, q):
            return decision(cat=Category.BEHAVIORAL)

    jev = BehavioralJev(chunks=("We saw a 20% increase and saved $50k.",))
    c, _ = make_controller(jev)
    flags = []
    c.answer_flags.connect(lambda g, f: flags.append(f))
    c.ask("Tell me about a time you improved performance.")
    qtbot.waitUntil(lambda: len(flags) == 1, timeout=3000)
    assert set(flags[0]) == {"20%", "$50k"}


def test_answer_flags_nothing_when_the_number_is_in_the_profile(qtbot, make_controller):
    class BehavioralJev(FakeJev):
        async def decide(self, q):
            return decision(cat=Category.BEHAVIORAL)

    jev = BehavioralJev(chunks=("We saw a 20% increase.",), profile="reduced latency by 20%")
    c, _ = make_controller(jev)
    flags = []
    c.answer_flags.connect(lambda g, f: flags.append(f))
    c.ask("Tell me about a time you improved performance.")
    qtbot.waitUntil(lambda: flags == [[]], timeout=3000)


# ---------------- controller: screenshot attach (Step 20) ----------------
SHOT = b"\x89PNG-screenshot"


def test_attached_screenshot_goes_with_the_next_question_only(qtbot, make_controller):
    jev = FakeJev()
    c, rec = make_controller(jev)
    c.attach_screenshot(SHOT)
    c.ask("What is a mutex?")
    c.ask("What is a lock?")
    qtbot.waitUntil(lambda: rec.log.count(("busy", False)) == 2, timeout=3000)
    assert jev.images == [SHOT, None]


def test_screenshot_pending_signal_tracks_attach_and_consume(qtbot, make_controller):
    c, _ = make_controller(FakeJev())
    pending = []
    c.screenshot_pending.connect(pending.append)
    c.attach_screenshot(SHOT)
    c.ask("What is a mutex?")
    assert pending == [True, False]


def test_pausing_or_a_new_interview_drops_the_attached_screenshot(qtbot, make_controller):
    jev = FakeJev()
    c, rec = make_controller(jev)
    pending = []
    c.screenshot_pending.connect(pending.append)
    c.attach_screenshot(SHOT)
    c.set_paused(True)
    assert pending == [True, False]
    c.attach_screenshot(SHOT)  # ignored while paused: nothing may be queued for later
    assert pending == [True, False]
    c.set_paused(False)
    c.ask("What is a mutex?")
    qtbot.waitUntil(lambda: rec.has("busy", False), timeout=3000)
    assert jev.images == [None]

    c.attach_screenshot(SHOT)
    c.new_interview()
    c.ask("What is a lock?")
    qtbot.waitUntil(lambda: rec.log.count(("busy", False)) == 2, timeout=3000)
    assert jev.images == [None, None]


def test_manual_trigger_also_sends_the_attached_screenshot(qtbot, make_controller):
    jev = FakeJev()
    clock = Clock()
    agg_ = UtteranceAggregator(quiet_s=0.15, clock=clock)
    c, rec = make_controller(jev, aggregator=agg_, answer_mode="manual")
    pending = []
    c.question_pending.connect(lambda text, lag: pending.append(text))
    c.on_speech_started()
    c.on_transcript_event(TranscriptEvent(1, "What is a mutex?", True, 2))
    clock.t += 0.3
    qtbot.waitUntil(lambda: pending == ["What is a mutex?"], timeout=3000)
    c.attach_screenshot(SHOT)
    c.trigger_answer()
    qtbot.waitUntil(lambda: rec.has("busy", False), timeout=3000)
    assert jev.images == [SHOT]


def test_redo_of_a_screenshot_question_keeps_the_screenshot(qtbot, make_controller):
    jev = FakeJev(chunks=("fixed",))
    c, rec = make_controller(jev)
    c.attach_screenshot(SHOT)
    c.ask("Find the second highest salary?")
    qtbot.waitUntil(lambda: rec.has("busy", False), timeout=3000)
    c.correct_category(Category.DATA_SQL)
    qtbot.waitUntil(lambda: rec.log.count(("busy", False)) == 2, timeout=3000)
    assert jev.images == [SHOT, SHOT]


# ---------------- overlay ----------------
@pytest.fixture
def overlay(qtbot, tmp_path):
    s = QSettings(str(tmp_path / "o.ini"), QSettings.Format.IniFormat)
    o = OverlayWindow(settings=s)
    qtbot.addWidget(o)
    return o


def test_overlay_window_flags_and_visibility_contract(overlay):
    f = overlay.windowFlags()
    assert f & Qt.WindowType.FramelessWindowHint and f & Qt.WindowType.WindowStaysOnTopHint
    src = pathlib.Path(__file__).parents[1] / "src" / "interview_assistant"
    for py in src.rglob("*.py"):
        assert "SetWindowDisplayAffinity" not in py.read_text(encoding="utf-8"), py


def test_overlay_streams_and_ignores_stale(qtbot, overlay):
    overlay.on_question(2, "What is a mutex?", "typed")
    overlay.on_decision(2, decision(Category.CONCEPT))
    overlay.on_delta(1, "STALE")  # from a cancelled answer
    overlay.on_delta(2, "A **mutex** ")
    overlay.on_delta(2, "locks.")
    qtbot.waitUntil(lambda: "locks" in overlay.answer.toPlainText(), timeout=2000)
    assert (
        "STALE" not in overlay.answer.toPlainText() and overlay.answer_text == "A **mutex** locks."
    )
    assert "concept" in overlay.chip.text() and "What is a mutex?" in overlay.question.text()
    overlay.on_finished(2, "openai:gpt-4o", 0.8, 1.2)
    assert "openai:gpt-4o" in overlay.chip.text() and "0.8s" in overlay.chip.text()


def test_overlay_reset_and_error(qtbot, overlay):
    overlay.on_question(1, "q", "voice")
    overlay.on_delta(1, "partial answer")
    qtbot.waitUntil(lambda: "partial" in overlay.answer.toPlainText(), timeout=2000)
    overlay.on_reset(1)
    assert overlay.answer_text == "" and overlay.answer.toPlainText() == ""
    overlay.on_failed(1, "All AI providers failed")
    assert "All AI providers failed" in overlay.answer.toPlainText()


def test_overlay_question_pending_enables_answer_button(overlay):
    assert not overlay.answer_btn.isEnabled()
    overlay.on_question_pending("What is a mutex?", None)
    assert overlay.answer_btn.isEnabled()
    assert "What is a mutex?" in overlay.question.text()
    assert "press Answer" in overlay.chip.text()


def test_overlay_new_question_disables_answer_button_again(overlay):
    overlay.on_question_pending("What is a mutex?", None)
    assert overlay.answer_btn.isEnabled()
    overlay.on_question(1, "What is a mutex?", "voice")  # trigger_answer() led here
    assert not overlay.answer_btn.isEnabled()


def test_overlay_answer_button_calls_trigger_answer(qtbot, overlay):
    calls = []
    overlay._controller = type("C", (), {"trigger_answer": lambda self: calls.append(1)})()
    overlay.on_question_pending("What is a mutex?", None)
    overlay.answer_btn.click()
    assert calls == [1]


def test_overlay_live_text_and_listening_dot(overlay):
    overlay.on_live_text("tell me about")
    assert "tell me about" in overlay.question.text()
    overlay.set_listening(True)
    assert overlay.dot.toolTip() == "listening"
    overlay.set_listening(False)
    assert overlay.dot.toolTip() == "idle"


def test_overlay_collapse_and_opacity_persist(qtbot, tmp_path):
    ini = str(tmp_path / "p.ini")
    o = OverlayWindow(settings=QSettings(ini, QSettings.Format.IniFormat))
    qtbot.addWidget(o)
    o.show()
    o.toggle_collapsed()
    assert not o.body.isVisible() and o.height() <= 60
    o.toggle_collapsed()
    assert o.body.isVisible() and o.height() > 100
    o.opacity.setValue(60)
    assert o.windowOpacity() == pytest.approx(0.6, abs=0.01)
    o.close()
    o2 = OverlayWindow(settings=QSettings(ini, QSettings.Format.IniFormat))
    qtbot.addWidget(o2)
    assert o2.opacity.value() == 60


def test_overlay_chat_row_toggles(overlay):
    assert not overlay.chat_row.isVisibleTo(overlay)
    overlay.chat_btn.click()
    assert overlay.chat_row.isVisibleTo(overlay)
    overlay.chat_btn.click()
    assert not overlay.chat_row.isVisibleTo(overlay)
    overlay.chat_btn.click()  # a third click (same isVisible()-while-hidden pitfall as fix_row)
    assert overlay.chat_row.isVisibleTo(overlay)


def test_overlay_sending_a_chat_question_leaves_the_row_open(qtbot, overlay):
    """Unlike fix_row (a one-shot action), Chat is a small persistent box - sending a question
    should not close it."""
    overlay._controller = type("C", (), {"ask": lambda self, text, source: None})()
    overlay.chat_btn.click()
    overlay.input.setText("What is a mutex?")
    overlay.ask_btn.click()
    assert overlay.chat_row.isVisibleTo(overlay)


def test_overlay_toggle_bubble_hides_everything_but_the_dot(qtbot, tmp_path):
    o = OverlayWindow(settings=QSettings(str(tmp_path / "b.ini"), QSettings.Format.IniFormat))
    qtbot.addWidget(o)
    o.show()
    assert not o.bubbled
    o.toggle_bubble()
    assert o.bubbled
    assert o.dot.isVisibleTo(o)
    for w in (o.title, o.chip, o.cost_label, o.chat_btn, o.fix_btn, o.collapse_btn, o.close_btn):
        assert not w.isVisibleTo(o)
    assert (o.width(), o.height()) == (48, 48)
    o.toggle_bubble()
    assert not o.bubbled and o.title.isVisibleTo(o) and o.width() > 48


def test_overlay_bubble_click_restores_but_drag_does_not(qtbot, tmp_path):
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QMouseEvent

    o = OverlayWindow(settings=QSettings(str(tmp_path / "b2.ini"), QSettings.Format.IniFormat))
    qtbot.addWidget(o)
    o.show()
    o.toggle_bubble()

    def press_release(start: QPointF, end: QPointF) -> None:
        down = QMouseEvent(
            QMouseEvent.Type.MouseButtonPress,
            start,
            start,
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        o.mousePressEvent(down)
        up = QMouseEvent(
            QMouseEvent.Type.MouseButtonRelease,
            end,
            end,
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        )
        o.mouseReleaseEvent(up)

    press_release(QPointF(10, 10), QPointF(40, 10))  # moved -> a drag, stays bubbled
    assert o.bubbled
    press_release(QPointF(10, 10), QPointF(11, 10))  # barely moved -> a click, restores
    assert not o.bubbled


def test_overlay_copy(overlay):
    overlay.on_question(1, "q", "typed")
    overlay.on_delta(1, "copy me")
    overlay.copy_answer()
    assert QApplication.clipboard().text() == "copy me"


# ---------------- overlay: Q&A history stack ----------------
def complete_turn(overlay, gen: int, question: str, answer: str, source: str = "typed") -> None:
    """A full question->answer cycle, as the controller's real signals would drive it."""
    overlay.on_question(gen, question, source)
    overlay.on_delta(gen, answer)
    overlay.on_finished(gen, "p:m", 0.5, 1.0)


def test_nav_row_hidden_until_first_completed_answer(overlay):
    assert not overlay.nav_row.isVisibleTo(overlay)
    complete_turn(overlay, 1, "q1", "a1")
    assert overlay.nav_row.isVisibleTo(overlay)
    assert overlay.history_label.text() == "Live"
    assert overlay.prev_btn.isEnabled()  # one entry exists - there's somewhere to go back to
    assert not overlay.next_btn.isEnabled()  # already at the newest; nothing "after" live


def test_prev_next_navigate_past_answers_read_only(overlay):
    complete_turn(overlay, 1, "q1", "a1")
    complete_turn(overlay, 2, "q2", "a2")
    assert overlay.history_label.text() == "Live"
    assert overlay.answer.toPlainText() == "a2"

    overlay.prev_qa()  # one step back from live lands on the same (newest) entry, now labeled
    assert overlay.history_label.text() == "2 of 2"
    assert overlay.answer.toPlainText() == "a2" and "q2" in overlay.question.text()

    overlay.prev_qa()  # a second step reaches the genuinely older entry
    assert overlay.history_label.text() == "1 of 2"
    assert overlay.answer.toPlainText() == "a1" and "q1" in overlay.question.text()
    assert overlay.prev_btn.isEnabled() is False  # at the oldest entry
    assert overlay.next_btn.isEnabled()

    overlay.next_qa()
    overlay.next_qa()
    assert overlay.history_label.text() == "Live"
    assert overlay.answer.toPlainText() == "a2" and "q2" in overlay.question.text()
    assert not overlay.next_btn.isEnabled()

    overlay.next_qa()  # already live - a no-op, not an error
    assert overlay.history_label.text() == "Live"


def test_navigating_history_does_not_re_trigger_an_answer(overlay):
    calls = []
    overlay._controller = type("C", (), {"ask": lambda self, *a, **k: calls.append(a)})()
    complete_turn(overlay, 1, "q1", "a1")
    overlay.prev_qa()
    overlay.next_qa()
    assert calls == []


def test_copy_while_navigating_copies_the_past_answer_not_the_live_one(overlay):
    complete_turn(overlay, 1, "q1", "a1")
    complete_turn(overlay, 2, "q2", "a2")
    overlay.prev_qa()
    overlay.prev_qa()  # now at the genuinely older entry (see test_prev_next_... for why twice)
    overlay.copy_answer()
    assert QApplication.clipboard().text() == "a1"
    overlay.next_qa()
    overlay.next_qa()
    overlay.copy_answer()
    assert QApplication.clipboard().text() == "a2"


def test_streaming_deltas_do_not_hijack_a_navigated_history_view(overlay):
    """A new answer streaming in while the user is reviewing an older one must not yank the
    display back to live out from under them (on_delta/_render is guarded for this)."""
    complete_turn(overlay, 1, "q1", "a1")
    overlay.prev_qa()
    assert overlay.answer.toPlainText() == "a1"
    overlay.on_question(2, "q2", "typed")  # a genuinely new question DOES force back to live
    assert overlay.history_label.text() == "Live"
    overlay.on_delta(2, "a2-partial")
    overlay._render()  # normally fired by the coalescing timer
    assert overlay.answer.toPlainText() == "a2-partial"


def test_new_question_while_viewing_history_resets_to_live_first(overlay):
    complete_turn(overlay, 1, "q1", "a1")
    overlay.prev_qa()
    assert overlay.history_label.text() == "1 of 1"
    overlay.on_question(2, "q2", "voice")
    assert overlay.history_label.text() == "Live" and "q2" in overlay.question.text()


# ---------------- overlay: "wrong specialist?" fix control ----------------
def test_overlay_fix_button_gated_by_decision(overlay):
    assert not overlay.fix_btn.isEnabled()
    overlay.on_question(1, "q", "typed")
    overlay.on_decision(1, decision(Category.HR))
    assert overlay.fix_btn.isEnabled()
    assert overlay.category_combo.currentData() == "hr"  # pre-selected to the current category


def test_overlay_fix_button_disabled_for_non_question(overlay):
    overlay.on_question(1, "okay", "voice")
    overlay.on_decision(1, decision(ok=False))  # NOT_A_QUESTION
    assert not overlay.fix_btn.isEnabled()


def test_overlay_fix_row_toggles(overlay):
    overlay.on_question(1, "q", "typed")
    overlay.on_decision(1, decision(Category.CODING))
    assert not overlay.fix_row.isVisibleTo(overlay)
    overlay.fix_btn.click()
    assert overlay.fix_row.isVisibleTo(overlay)
    overlay.fix_btn.click()
    assert not overlay.fix_row.isVisibleTo(overlay)
    overlay.fix_btn.click()  # a third click (regression: isVisible() is unreliable when hidden)
    assert overlay.fix_row.isVisibleTo(overlay)


def test_overlay_new_question_hides_fix_row_and_resets_gating(overlay):
    overlay.on_question(1, "q", "typed")
    overlay.on_decision(1, decision(Category.CODING))
    overlay.fix_btn.click()
    overlay.on_question(2, "next", "typed")
    assert not overlay.fix_row.isVisibleTo(overlay) and not overlay.fix_btn.isEnabled()


def test_overlay_apply_fix_calls_controller_with_selected_category(overlay):
    calls = []
    overlay._controller = type("C", (), {"correct_category": lambda self, cat: calls.append(cat)})()
    overlay.on_question(1, "q", "typed")
    overlay.on_decision(1, decision(Category.HR))
    overlay.fix_btn.click()
    i = overlay.category_combo.findData("data_sql")
    overlay.category_combo.setCurrentIndex(i)
    overlay.fix_apply_btn.click()
    assert calls == [Category.DATA_SQL] and not overlay.fix_row.isVisibleTo(overlay)


def test_overlay_category_corrected_shows_on_chip_tooltip(overlay):
    overlay.on_category_corrected("q", "hr", "data_sql")
    assert "hr" in overlay.chip.toolTip() and "data_sql" in overlay.chip.toolTip()


# ---------------- overlay: "verify before saying" warning ----------------
def test_overlay_shows_and_clears_claim_warning(overlay):
    overlay.on_question(1, "q", "typed")
    assert not overlay.warn_label.isVisibleTo(overlay)
    overlay.on_flags(1, ["20%", "$50k"])
    assert overlay.warn_label.isVisibleTo(overlay)
    assert "20%" in overlay.warn_label.text() and "$50k" in overlay.warn_label.text()
    overlay.on_flags(1, [])
    assert not overlay.warn_label.isVisibleTo(overlay)


def test_overlay_stale_flags_are_ignored(overlay):
    overlay.on_question(2, "q", "typed")
    overlay.on_flags(1, ["20%"])
    assert not overlay.warn_label.isVisibleTo(overlay)


def test_overlay_reset_and_new_question_clear_the_warning(overlay):
    overlay.on_question(1, "q", "typed")
    overlay.on_flags(1, ["20%"])
    assert overlay.warn_label.isVisibleTo(overlay)
    overlay.on_reset(1)
    assert not overlay.warn_label.isVisibleTo(overlay)
    overlay.on_flags(1, ["20%"])
    overlay.on_question(2, "next", "typed")
    assert not overlay.warn_label.isVisibleTo(overlay)


# ---------------- overlay + controller + real Jev/Router (scripted providers) ----------------
def test_end_to_end_typed_question_shows_answer(qtbot, tmp_path):
    a = Script("a", ["Use a ", "hash map."])
    chain = [Route("a", "am")]
    router = Router({"a": a}, RouterConfig(chains={"fast": chain, "smart": chain}))
    controller = AssistantController(Jev(router, JevConfig(profile="5y Python")), poll_ms=10)
    s = QSettings(str(tmp_path / "e.ini"), QSettings.Format.IniFormat)
    o = OverlayWindow(controller, s)
    qtbot.addWidget(o)
    o.show()
    o.chat_btn.click()  # Chat is toggled off by default (Step 15)
    qtbot.keyClicks(o.input, "Write a function to solve the two sum problem.")
    qtbot.keyClick(o.input, Qt.Key.Key_Return)
    qtbot.waitUntil(lambda: "hash map." in o.answer.toPlainText(), timeout=5000)
    qtbot.waitUntil(lambda: "a:am" in o.chip.text(), timeout=3000)
    assert o.chip.text().startswith("coding")
    assert not o.stop_btn.isEnabled()
    controller.shutdown()


# ---------------- overlay: screenshot button (Step 20) ----------------
class ShotController:
    def __init__(self):
        self.attached = []

    def attach_screenshot(self, image):
        self.attached.append(image)


def test_overlay_screenshot_button_attaches_a_capture(overlay, monkeypatch):
    import interview_assistant.ui.overlay as overlay_mod

    monkeypatch.setattr(overlay_mod, "capture_screen_png", lambda: SHOT)
    ctl = ShotController()
    overlay._controller = ctl
    overlay.screenshot_btn.click()
    assert ctl.attached == [SHOT]


def test_overlay_screenshot_is_blocked_without_consent(overlay, monkeypatch):
    import interview_assistant.ui.overlay as overlay_mod

    monkeypatch.setattr(overlay_mod, "capture_screen_png", lambda: pytest.fail("captured"))
    ctl = ShotController()
    overlay._controller = ctl
    overlay.before_screenshot = lambda: False
    overlay.screenshot_btn.click()
    assert ctl.attached == []


def test_overlay_screenshot_with_no_capture_available_attaches_nothing(overlay, monkeypatch):
    import interview_assistant.ui.overlay as overlay_mod

    monkeypatch.setattr(overlay_mod, "capture_screen_png", lambda: None)
    ctl = ShotController()
    overlay._controller = ctl
    overlay.screenshot_btn.click()
    assert ctl.attached == []


def test_overlay_screenshot_without_a_controller_is_a_no_op(overlay):
    overlay._controller = None
    overlay.take_screenshot()  # must not raise


def test_overlay_screenshot_button_shows_pending_state(overlay):
    assert overlay.screenshot_btn.text().startswith("Screenshot")
    assert "✓" not in overlay.screenshot_btn.text()
    overlay.on_screenshot_pending(True)
    assert "✓" in overlay.screenshot_btn.text()
    assert "next question" in overlay.screenshot_btn.toolTip()
    overlay.on_screenshot_pending(False)
    assert "✓" not in overlay.screenshot_btn.text()


def test_overlay_screenshot_button_hides_in_bubble_mode(qtbot, tmp_path):
    o = OverlayWindow(settings=QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat))
    qtbot.addWidget(o)
    o.toggle_bubble()
    assert not o.screenshot_btn.isVisibleTo(o)
    o.toggle_bubble()
    assert o.screenshot_btn.isVisibleTo(o)


def test_capture_screen_png_returns_png_bytes_or_nothing(qtbot):
    from interview_assistant.ui.screenshot import capture_screen_png

    image = capture_screen_png()
    assert image is None or image.startswith(b"\x89PNG\r\n\x1a\n")


def test_typed_text_is_answered_even_when_it_is_not_phrased_as_a_question(qtbot, make_controller):
    jev = FakeJev()
    c, rec = make_controller(jev)
    c.ask("Okay, write prime code")  # FakeJev classifies "okay..." as NOT_A_QUESTION
    qtbot.waitUntil(lambda: rec.has("busy", False), timeout=3000)
    assert jev.asked == ["Okay, write prime code"]
    assert rec.has("done", 1, "p:m")


def test_answer_button_answers_a_spoken_phrase_the_classifier_calls_not_a_question(
    qtbot, make_controller
):
    jev = FakeJev()
    clock = Clock()
    agg_ = UtteranceAggregator(quiet_s=0.15, clock=clock)
    c, rec = make_controller(jev, aggregator=agg_, answer_mode="manual")
    pending = []
    c.question_pending.connect(lambda text, lag: pending.append(text))
    phrase = "Okay, difference between a list and a tuple."  # FakeJev: "okay..." is not a question
    c.on_speech_started()
    c.on_transcript_event(TranscriptEvent(1, phrase, True, 2))
    clock.t += 0.3
    qtbot.waitUntil(lambda: pending == [phrase], timeout=3000)
    c.trigger_answer()
    qtbot.waitUntil(lambda: rec.has("busy", False), timeout=3000)
    assert jev.asked == [phrase]


def test_answer_with_nothing_waiting_says_so_instead_of_doing_nothing(overlay):
    calls = []
    overlay._controller = type("C", (), {"trigger_answer": lambda self: calls.append(1)})()
    overlay.answer_now()
    assert calls == [] and "no question waiting" in overlay.chip.text()
