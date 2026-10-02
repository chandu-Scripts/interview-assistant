import asyncio
import json
from datetime import datetime

import numpy as np
import pytest
from PySide6.QtCore import QSettings

from interview_assistant.agents.jev import Jev, JevConfig
from interview_assistant.app.controller import AssistantController
from interview_assistant.config.settings import Settings
from interview_assistant.cost.factory import build_tracker
from interview_assistant.cost.pricing import PriceBook, TokenPrice
from interview_assistant.cost.tracker import (
    Budget,
    BudgetLevel,
    CostTracker,
    UsageRecord,
    UsageStore,
    format_usd,
)
from interview_assistant.llm.router import AttemptRecord, Route, Router, RouterConfig
from interview_assistant.llm.types import ErrorKind, LLMError, LLMRequest, Message, Usage
from interview_assistant.stt.metered import MeteredSTT
from interview_assistant.stt.streaming import StreamingTranscriber
from interview_assistant.ui.overlay import COST_COLOR, OverlayWindow
from tests.test_router import Script

REQ = LLMRequest([Message("user", "Hi")], system="sys")


DEFAULT_USAGE = Usage(1000, 500)


def attempt(provider="openai", model="gpt-4o", usage=DEFAULT_USAGE, **kw):
    defaults = dict(attempt=0, ok=True, latency_s=1.0)
    defaults.update(kw)
    return AttemptRecord(Route(provider, model), usage=usage, **defaults)


class Clock:
    def __init__(self, dt=datetime(2026, 9, 27, 12, 0)):
        self.t = dt.timestamp()

    def __call__(self):
        return self.t


# ---------------- pricing ----------------
def test_price_lookup_prefers_longest_prefix():
    pb = PriceBook()
    assert pb.token_price("openai", "gpt-4o-mini") == TokenPrice(0.15, 0.60)
    assert pb.token_price("openai", "gpt-4o-2024-08-06") == TokenPrice(2.50, 10.00)
    assert pb.token_price("anthropic", "claude-haiku-4-5-20251001") == TokenPrice(1.0, 5.0)
    assert pb.token_price("anthropic", "claude-sonnet-5") == TokenPrice(2.0, 10.0)
    assert pb.token_price("anthropic", "claude-opus-5-5") == TokenPrice(4.0, 20.0)
    assert pb.token_price("gemini", "gemini-3.1-flash-lite") == TokenPrice(0.25, 1.50)
    assert pb.token_price("gemini", "gemini-2.5-flash-lite") == TokenPrice(0.10, 0.40)


def test_unknown_model_is_none_not_zero():
    pb = PriceBook()
    assert pb.llm_cost("openai", "gpt-99", 1000, 1000) is None
    assert pb.llm_cost("nobody", "x", 1, 1) is None
    assert pb.stt_cost("openai", "mystery", 60) is None


def test_cost_math():
    pb = PriceBook()
    # gpt-4o: 1000 in * $2.5/M + 500 out * $10/M = 0.0025 + 0.005
    assert pb.llm_cost("openai", "gpt-4o", 1000, 500) == pytest.approx(0.0075)
    assert pb.stt_cost("openai", "gpt-4o-mini-transcribe", 90) == pytest.approx(0.0045)
    assert pb.stt_cost("local-whisper", "base.en", 3600) == 0.0


def test_pricing_overrides(tmp_path):
    f = tmp_path / "p.json"
    f.write_text(
        json.dumps(
            {
                "tokens": {"openai:gpt-4o": {"input": 1, "output": 2}, "bad": {"input": "x"}},
                "audio_per_min": {"openai:whisper-1": 0.01},
            }
        )
    )
    pb = PriceBook()
    assert pb.load_overrides(f) == 2
    assert pb.llm_cost("openai", "gpt-4o", 1_000_000, 1_000_000) == pytest.approx(3.0)
    assert pb.stt_cost("openai", "whisper-1", 60) == pytest.approx(0.01)
    assert pb.load_overrides(tmp_path / "missing.json") == 0


# ---------------- tracker ----------------
def test_tracker_totals_and_breakdown():
    t = CostTracker(PriceBook())
    t.record_attempt(attempt(tag="coding"))
    t.record_attempt(attempt("openai", "gpt-4o-mini", Usage(2000, 100), tag="classify"))
    t.record_stt("openai", "gpt-4o-mini-transcribe", 30)
    s = t.summary()
    assert s.calls == 3 and s.input_tokens == 3000 and s.output_tokens == 600
    assert s.llm_usd == pytest.approx(0.0075 + 0.0003 + 0.00006)
    assert s.stt_usd == pytest.approx(0.0015)
    assert s.total_usd == pytest.approx(s.llm_usd + s.stt_usd)
    assert set(s.by_tag) == {"coding", "classify"}
    assert set(s.by_model) == {
        "openai:gpt-4o",
        "openai:gpt-4o-mini",
        "openai:gpt-4o-mini-transcribe",
    }
    assert s.by_provider["openai"] == pytest.approx(s.total_usd)
    assert s.unpriced_calls == 0 and s.wasted_usd == 0


def test_unpriced_calls_flagged_and_excluded():
    t = CostTracker(PriceBook())
    t.record_attempt(attempt("openai", "gpt-99"))
    s = t.summary()
    assert s.unpriced_calls == 1 and s.total_usd == 0 and s.input_tokens == 1000
    assert format_usd(s.total_usd, True).endswith("+")


def test_failed_and_cancelled_attempts_are_counted_as_waste_with_estimates():
    t = CostTracker(PriceBook())
    # died mid-stream after 400 chars of output, provider never sent usage -> estimated
    t.record_attempt(
        attempt(
            usage=Usage(),
            ok=False,
            error=LLMError("openai", ErrorKind.SERVER, "x"),
            input_chars=4000,
            output_chars=400,
        )
    )
    # cancelled by a new question
    t.record_attempt(
        attempt(usage=Usage(), ok=False, cancelled=True, input_chars=800, output_chars=80)
    )
    # failed before any output (e.g. 429): costs nothing
    t.record_attempt(attempt(usage=Usage(), ok=False, input_chars=4000, output_chars=0))
    s = t.summary()
    assert s.input_tokens == 1000 + 200 and s.output_tokens == 100 + 20
    assert s.wasted_usd == pytest.approx(s.total_usd) and s.total_usd > 0
    rec = t._records
    assert rec[0].estimated and rec[1].cancelled and not rec[2].estimated


def test_sessions_and_time_scopes():
    clk = Clock()
    t = CostTracker(PriceBook(), clock=clk)
    t.record_attempt(attempt())
    t.new_session()
    t.record_attempt(attempt())
    assert t.summary("session").calls == 1 and t.summary("all").calls == 2
    clk.t += 86400 * 3  # three days later, same month
    t.record_attempt(attempt())
    assert t.summary("day").calls == 1 and t.summary("month").calls == 3
    clk.t += 86400 * 40  # next month
    assert t.summary("month").calls == 0


def test_store_persists_and_survives_corrupt_lines(tmp_path):
    path = tmp_path / "sub" / "usage.jsonl"
    clk = Clock()
    t1 = CostTracker(PriceBook(), UsageStore(path), clock=clk)
    t1.record_attempt(attempt(tag="coding"))
    t1.record_stt("openai", "whisper-1", 60)
    with path.open("a", encoding="utf-8") as f:
        f.write('not json\n{"foo": 1}\n')
    t2 = CostTracker(PriceBook(), UsageStore(path), clock=clk)  # a later run
    assert t2.summary("month").calls == 2 and t2.summary("session").calls == 0
    raw = path.read_text(encoding="utf-8")
    assert "Hi" not in raw and "prompt" not in raw  # metadata only
    assert set(json.loads(raw.splitlines()[0])) == set(UsageRecord.__dataclass_fields__)


def test_old_months_not_loaded(tmp_path):
    path = tmp_path / "u.jsonl"
    old = Clock(datetime(2026, 8, 15))
    CostTracker(PriceBook(), UsageStore(path), clock=old).record_attempt(attempt())
    now = CostTracker(PriceBook(), UsageStore(path), clock=Clock(datetime(2026, 9, 27)))
    assert now.summary("all").calls == 0


def test_on_change_called_and_errors_swallowed():
    calls = []
    t = CostTracker(PriceBook(), on_change=lambda: calls.append(1))
    t.record_attempt(attempt())
    t.new_session()
    assert len(calls) == 2
    t.on_change = lambda: 1 / 0
    t.record_attempt(attempt())  # must not raise


# ---------------- budget ----------------
def test_budget_levels_and_hard_stop():
    t = CostTracker(PriceBook(), budget=Budget(0.01, "session", hard_stop=True))
    assert t.budget_state().level is BudgetLevel.OK and not t.budget_state().blocked
    t.record_attempt(attempt(usage=Usage(2000, 300)))  # 0.005 + 0.003 = 0.008 -> 80%
    assert t.budget_state().level is BudgetLevel.WARN
    t.record_attempt(attempt(usage=Usage(1000, 100)))
    st = t.budget_state()
    assert st.level is BudgetLevel.EXCEEDED and st.blocked
    t.new_session()  # session-scoped budget resets
    assert t.budget_state().level is BudgetLevel.OK


def test_budget_soft_limit_never_blocks_and_no_budget():
    t = CostTracker(PriceBook(), budget=Budget(0.001))
    t.record_attempt(attempt())
    assert t.budget_state().level is BudgetLevel.EXCEEDED and not t.budget_state().blocked
    assert CostTracker().budget_state().limit_usd is None


def test_daily_budget_survives_new_session():
    t = CostTracker(PriceBook(), budget=Budget(0.001, "day"))
    t.record_attempt(attempt())
    t.new_session()
    assert t.budget_state().level is BudgetLevel.EXCEEDED


def test_build_tracker_from_settings(tmp_path, monkeypatch):
    s = Settings(_env_file=None, budget_usd=2.5, budget_hard_stop=True, data_dir=str(tmp_path))
    t = build_tracker(s)
    assert t.budget and t.budget.limit_usd == 2.5 and t.budget.hard_stop
    t.record_attempt(attempt())
    assert (tmp_path / "usage.jsonl").exists()
    assert build_tracker(Settings(_env_file=None, usage_log=False, budget_usd=0)).budget is None


# ---------------- STT metering / partials ----------------
class FakeSTT:
    name = "openai"
    model = "gpt-4o-mini-transcribe"
    metered = True

    def __init__(self, fail=False):
        self.fail, self.calls = fail, 0

    def warmup(self):
        pass

    def transcribe(self, pcm):
        self.calls += 1
        if self.fail:
            raise RuntimeError("net")
        return "text"


def test_metered_stt_reports_seconds_even_on_failure():
    seen = []
    ok = MeteredSTT(FakeSTT(), lambda p, m, s: seen.append((p, m, s)))
    ok.transcribe(np.zeros(32000, dtype=np.int16))
    bad = MeteredSTT(FakeSTT(fail=True), lambda p, m, s: seen.append((p, m, s)))
    with pytest.raises(RuntimeError):
        bad.transcribe(np.zeros(16000, dtype=np.int16))
    assert seen == [
        ("openai", "gpt-4o-mini-transcribe", 2.0),
        ("openai", "gpt-4o-mini-transcribe", 1.0),
    ]


def test_partials_disabled_for_billed_stt():
    stt = FakeSTT()
    events = []
    tr = StreamingTranscriber(stt, events.append, partials=False)
    tr.start()
    tr.speech_started()
    tr.submit_partial(np.zeros(16000, dtype=np.int16))
    tr.submit_final(np.zeros(16000, dtype=np.int16))
    tr.stop()
    assert stt.calls == 1 and [e.is_final for e in events] == [True]


# ---------------- router -> tracker integration ----------------
async def test_router_records_tag_failure_and_cancellation():
    recs: list[AttemptRecord] = []
    a = Script("a", ["par", "tial", LLMError("a", ErrorKind.SERVER, "x")])
    b = Script("b", ["ok"])
    chain = [Route("a", "am"), Route("b", "bm")]
    r = Router({"a": a, "b": b}, RouterConfig(chains={"fast": chain}), recs.append)
    async for _ in r.stream(REQ, "fast", tag="coding"):
        pass
    assert [x.ok for x in recs] == [False, True] and {x.tag for x in recs} == {"coding"}
    assert recs[0].output_chars == 7 and recs[0].input_chars == len("sys") + len("Hi")

    recs.clear()
    slow = Script("s", ["x", ("sleep", 10)])
    r2 = Router({"s": slow}, RouterConfig(chains={"fast": [Route("s", "m")]}), recs.append)

    async def consume():
        async for _ in r2.stream(REQ, "fast", tag="t"):
            pass

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(recs) == 1 and recs[0].cancelled and recs[0].output_chars == 1


# ---------------- controller + overlay ----------------
def make_stack(qtbot, tmp_path, budget=None, steps=("Use a ", "hash map.")):
    tracker = CostTracker(PriceBook(), budget=budget)
    a = Script("openai", list(steps), model="gpt-4o")
    chain = [Route("openai", "gpt-4o")]
    router = Router(
        {"openai": a}, RouterConfig(chains={"fast": chain, "smart": chain}), tracker.record_attempt
    )
    c = AssistantController(Jev(router, JevConfig()), poll_ms=10, tracker=tracker)
    tracker.on_change = c.notify_cost_changed
    o = OverlayWindow(c, QSettings(str(tmp_path / "o.ini"), QSettings.Format.IniFormat))
    qtbot.addWidget(o)
    return tracker, c, o


def test_overlay_shows_answer_cost_and_session_total(qtbot, tmp_path):
    tracker, c, o = make_stack(qtbot, tmp_path)
    c.ask("Write a function to solve the two sum problem.")
    qtbot.waitUntil(lambda: "openai:gpt-4o" in o.chip.text(), timeout=5000)
    assert "$" in o.chip.text()  # per-answer cost in the chip
    assert o.cost_label.text().startswith("$") and tracker.summary().calls == 1
    assert "openai:gpt-4o" in o.cost_label.toolTip()
    c.shutdown()


def test_hard_budget_blocks_new_answers(qtbot, tmp_path):
    tracker, c, o = make_stack(qtbot, tmp_path, budget=Budget(0.0000001, hard_stop=True))
    c.ask("Write a function to solve the two sum problem.")
    qtbot.waitUntil(lambda: tracker.summary().calls == 1, timeout=5000)
    qtbot.waitUntil(lambda: "$" in o.cost_label.text(), timeout=2000)
    assert tracker.budget_state().blocked
    c.ask("Explain how garbage collection works?")
    qtbot.waitUntil(lambda: "Budget reached" in o.answer.toPlainText(), timeout=5000)
    assert tracker.summary().calls == 1  # no second LLM call was made
    assert COST_COLOR[BudgetLevel.EXCEEDED] in o.cost_label.styleSheet()
    c.shutdown()


def test_new_interview_resets_session_and_history(qtbot, tmp_path):
    tracker, c, o = make_stack(qtbot, tmp_path)
    c.ask("Write a function to solve the two sum problem.")
    qtbot.waitUntil(lambda: tracker.summary().calls == 1, timeout=5000)
    qtbot.waitUntil(lambda: "hash map." in o.answer.toPlainText(), timeout=3000)
    c.new_interview()
    assert tracker.summary("session").calls == 0 and tracker.summary("all").calls == 1
    c.shutdown()
