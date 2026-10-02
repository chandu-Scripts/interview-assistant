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
    assert a.poll() == "Tell me about a conflict."
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
    assert a.poll() == "Tell me about a time you resolved a conflict. What was the outcome?"


def test_aggregator_max_hold_and_stale_in_flight():
    a, c = agg(quiet_s=1.0, max_hold_s=8.0, stale_s=20.0)
    a.speech_started()
    a.final("Part one.")
    a.speech_started()  # never completes (STT error)
    c.t += 7
    assert a.poll() is None
    c.t += 2  # max hold exceeded
    assert a.poll() == "Part one."
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
        self.cleared = 0
        self.config = FakeJevConfig(profile)

    async def decide(self, q):
        await asyncio.sleep(self.delays.get(q, 0))
        return decision(ok=not q.lower().startswith("okay"))

    async def ask(self, q, decision=None):
        self.asked.append(q)
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
    qtbot.waitUntil(lambda: rec.has("done", 2, "p:m"), timeout=15000)
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
    c, rec = make_controller(
        jev, aggregator=UtteranceAggregator(quiet_s=0.2), answer_mode="auto"
    )
    c.on_speech_started()
    c.on_transcript_event(TranscriptEvent(1, "Tell me about a time you failed.", True, 3))
    c.on_speech_started()  # second fragment still transcribing
    qtbot.wait(400)
    assert jev.asked == []  # held back
    c.on_transcript_event(TranscriptEvent(2, "What did you learn?", True, 2))
    qtbot.waitUntil(lambda: rec.has("busy", False), timeout=3000)
    assert jev.asked == ["Tell me about a time you failed. What did you learn?"]
    assert rec.has("q", 1, "Tell me about a time you failed. What did you learn?", "voice")


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
    c, rec = make_controller(
        jev, aggregator=UtteranceAggregator(quiet_s=0.15), answer_mode="auto"
    )
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
    qtbot.waitUntil(lambda: jev.asked == ["Find the second highest salary?"] * 2, timeout=3000)
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


def test_overlay_copy(overlay):
    overlay.on_question(1, "q", "typed")
    overlay.on_delta(1, "copy me")
    overlay.copy_answer()
    assert QApplication.clipboard().text() == "copy me"


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
    qtbot.keyClicks(o.input, "Write a function to solve the two sum problem.")
    qtbot.keyClick(o.input, Qt.Key.Key_Return)
    qtbot.waitUntil(lambda: "hash map." in o.answer.toPlainText(), timeout=5000)
    qtbot.waitUntil(lambda: "a:am" in o.chip.text(), timeout=3000)
    assert o.chip.text().startswith("coding")
    assert not o.stop_btn.isEnabled()
    controller.shutdown()
