import asyncio
import itertools
import logging
import time
from contextvars import ContextVar
from typing import Literal, Protocol

from PySide6.QtCore import QObject, QTimer, Signal

from interview_assistant.agents.types import Category, Decision
from interview_assistant.agents.verify import CLAIM_PRONE, find_unverified_claims
from interview_assistant.app.aggregator import UtteranceAggregator
from interview_assistant.app.async_runner import AsyncRunner
from interview_assistant.cost.tracker import CostTracker, format_usd
from interview_assistant.history.store import HistoryStore, Status, TurnRecord
from interview_assistant.llm.router import AllRoutesFailed, NoRoutesAvailable, RouteEvent
from interview_assistant.stt.base import TranscriptEvent

log = logging.getLogger(__name__)


DETECTED_QUESTION_PREFIX = "Question:"
SCREENSHOT_PROMPT = (
    "Look at this screenshot. Start with one line beginning '"
    + DETECTED_QUESTION_PREFIX
    + "' followed by the question or task shown on screen, cleaned up. Then a blank line, then "
    "answer it. If it is a coding problem, write the solution."
)


class JevLike(Protocol):
    async def decide(self, question: str) -> Decision: ...

    def ask(
        self, question: str, decision: Decision | None = None, image: bytes | None = None
    ): ...  # async generator

    def clear_history(self) -> None: ...

    def manual_decision(self, category: Category) -> Decision: ...


def friendly_error(e: BaseException) -> str:
    if isinstance(e, NoRoutesAvailable):
        return "No LLM provider is configured. Add an API key (OPENAI_API_KEY, ...) to .env."
    if isinstance(e, AllRoutesFailed):
        return "All AI providers failed: " + "; ".join(str(x)[:90] for x in e.errors[:3])
    return f"{type(e).__name__}: {e}"[:300]


class AssistantController(QObject):
    """Glue between transcripts, Jev and the UI.

    Every signal carries a generation number `gen`; a view must ignore signals whose gen is older
    than the last question_started it saw. Voice questions that Jev judges to be filler are
    ignored without disturbing the answer on screen.
    """

    question_started = Signal(int, str, str)  # gen, text, source ("voice" | "typed")
    question_pending = Signal(str, object)  # text, speech_lag (float | None) - manual mode only
    decision_ready = Signal(int, object)  # gen, Decision
    answer_delta = Signal(int, str)
    answer_reset = Signal(int)  # discard text shown so far (provider failover)
    answer_finished = Signal(int, str, float, float)  # gen, route, first_token_s, total_s
    answer_failed = Signal(int, str)
    answer_stopped = Signal(int)
    question_ignored = Signal(str)
    live_text = Signal(str)  # partial transcript of the ongoing utterance
    busy_changed = Signal(bool)
    answer_cost = Signal(int, object)  # gen, USD spent on this question (float | None)
    latency_ready = Signal(int, object)  # gen, dict of stage timings in seconds
    cost_updated = Signal(object, object)  # Summary (session), BudgetState
    answer_flags = Signal(int, list)  # gen, unverified numeric claims found in the answer
    category_corrected = Signal(str, str, str)  # question, original category, corrected category
    screenshot_pending = Signal(bool)  # a screenshot is attached to the next question

    def __init__(
        self,
        jev: JevLike,
        runner: AsyncRunner | None = None,
        aggregator: UtteranceAggregator | None = None,
        poll_ms: int = 100,
        tracker: CostTracker | None = None,
        history: HistoryStore | None = None,
        answer_mode: Literal["manual", "auto"] = "manual",
    ) -> None:
        super().__init__()
        self._jev = jev
        self._tracker = tracker
        self._history = history
        self._answer_mode = answer_mode
        self._pending_question: str | None = None
        self._pending_lag: float | None = None
        self._pending_source: str = ""
        self._stopped_gen = 0
        self._paused = False
        self._last_speech_end: dict[str, float] = {}
        # Set just before each answer starts (loop thread only); lets correct_category() redo
        # the most recent question with a different specialist.
        self._last_question: str | None = None
        self._last_source: str = "typed"
        self._last_decision: Decision | None = None
        self._last_image: bytes | None = None
        self._pending_image: bytes | None = None
        self._runner = runner or AsyncRunner()
        self._agg = aggregator or UtteranceAggregator()
        self._gen = itertools.count(1)
        # Touched only on the runner's loop thread. Keyed by source ("typed"/"voice"/
        # "voice:mic"/"voice:loopback") so that two simultaneous sources (Step 19's dual audio
        # capture) never cancel or drop each other's in-flight answer - only a newer question
        # from the SAME source supersedes an older one, matching the single-source behavior this
        # replaces. `_active_gen`/`_active_source` track whichever answer is most recently active
        # across all sources, for the single "stop" button/UI pane.
        self._committed_gen: dict[str, int] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._runs: dict[int, asyncio.Task] = {}
        self._invalid_before = 0
        self._write_generation: ContextVar[int | None] = ContextVar(
            "history_generation", default=None
        )
        self._active_gen = 0
        self._active_source = ""
        self._timer = QTimer(self)
        self._timer.setInterval(poll_ms)
        self._timer.timeout.connect(self._poll)
        self._timer.start()

    # ---- inputs (UI thread) ----
    def on_speech_started(self, source: str = "") -> None:
        """`source`: "" for the app's single (today's default) audio source, or a DeviceKind's
        value ("mic"/"loopback") when Step 19's simultaneous dual-source capture is active -
        must match what on_transcript_event()'s events report, so they merge independently."""
        if not self._paused:
            self._agg.speech_started(source)

    def on_transcript_event(self, ev: TranscriptEvent) -> None:
        if self._paused:
            return
        source = ev.source.value if ev.source is not None else ""
        if ev.is_final:
            self._agg.final(ev.text, source)
            self.live_text.emit("")
        else:
            self.live_text.emit(ev.text)

    def set_paused(self, paused: bool) -> None:
        """While paused (presentation mode) no new question is accepted, voice or typed."""
        self._paused = paused
        if paused:
            self._invalidate_answers()
            self._agg.reset()
            self._pending_question = None
            self._pending_lag = None
            self._pending_source = ""
            self._drop_pending_image()

    @property
    def paused(self) -> bool:
        return self._paused

    def on_speech_ended(self, source: str = "") -> None:
        self._last_speech_end[source] = time.monotonic()
        self._agg.speech_ended(source)

    def attach_screenshot(self, image: bytes) -> None:
        """Answer the NEXT question (typed or voice, in either mode) with this screenshot, once."""
        if self._paused:
            return
        self._pending_image = image
        self.screenshot_pending.emit(True)

    def ask_screenshot(self, image: bytes) -> None:
        """Answer whatever the screenshot shows, right away - no follow-up question needed."""
        self.attach_screenshot(image)
        self.ask(SCREENSHOT_PROMPT, "screenshot", explicit=True)

    def _drop_pending_image(self) -> None:
        if self._pending_image is not None:
            self._pending_image = None
            self.screenshot_pending.emit(False)

    def ask(
        self,
        text: str,
        source: str = "typed",
        speech_lag: float | None = None,
        explicit: bool = False,
    ) -> None:
        """`speech_lag`: seconds from the end of the speech to now (voice questions only).
        `explicit`: the user asked for this answer (Answer button), so it is never dropped as
        "not a question"."""
        text = text.strip()
        if text and not self._paused:
            gen = next(self._gen)
            image, self._pending_image = self._pending_image, None
            if image is not None:
                self.screenshot_pending.emit(False)
            self._runner.submit(
                self._execute(
                    gen,
                    text,
                    source,
                    speech_lag,
                    image,
                    explicit,
                    history_generation=self._history_generation(),
                )
            )

    def set_answer_mode(self, mode: Literal["manual", "auto"]) -> None:
        self._answer_mode = mode

    def stop(self) -> None:
        self._runner.call_soon(self._stop_active)

    def trigger_answer(self) -> None:
        """Manual mode: answer the question currently waiting (see question_pending). A no-op if
        nothing is pending (e.g. the button was clicked twice, or the UI is stale)."""
        if self._pending_question is None or self._paused:
            return
        text, lag, source = self._pending_question, self._pending_lag, self._pending_source
        self._pending_question = None
        self._pending_lag = None
        self._pending_source = ""
        self.ask(text, "voice" if not source else f"voice:{source}", lag, explicit=True)

    def correct_category(self, category: Category) -> None:
        """The user says Jev picked the wrong specialist: redo the last question with this one.

        Picking the SAME category again is a plain "regenerate" and is not logged as a correction.
        """
        if self._paused:
            return
        gen = next(self._gen)
        self._runner.submit(
            self._execute(
                gen,
                category=category,
                history_generation=self._history_generation(),
            )
        )

    def clear_history(self) -> None:
        self._jev.clear_history()

    def new_interview(self) -> None:
        """Forget the conversation and start a fresh cost session."""
        self._invalidate_answers()
        self._last_question = None
        self._last_image = None
        self._jev.clear_history()
        self._agg.reset()
        self._pending_question = None
        self._pending_lag = None
        self._pending_source = ""
        self._drop_pending_image()
        if self._tracker is not None:
            self._tracker.new_session()
        if self._history is not None:
            self._history.end_session()

    def notify_cost_changed(self) -> None:
        """Tracker on_change hook; safe from any thread."""
        if self._tracker is not None:
            self.cost_updated.emit(self._tracker.summary("session"), self._tracker.budget_state())

    def shutdown(self) -> None:
        self._timer.stop()
        self._runner.stop()
        if self._history is not None:
            self._history.end_session()

    def _poll(self) -> None:
        if self._paused:
            self._agg.reset()
            return
        if (result := self._agg.poll()) is not None:
            question, source = result
            lag = None
            ended_at = self._last_speech_end.get(source)
            if ended_at is not None:
                lag = max(0.0, time.monotonic() - ended_at)
            if self._answer_mode == "manual":
                # Waits for trigger_answer() instead of answering immediately. A later poll()
                # before that happens simply replaces the pending question (latest wins).
                self._pending_question, self._pending_lag, self._pending_source = (
                    question,
                    lag,
                    source,
                )
                self.question_pending.emit(question, lag)
            else:
                self.ask(question, "voice" if not source else f"voice:{source}", lag)

    # ---- runs on the asyncio thread ----
    def _history_generation(self) -> int | None:
        if self._history is None:
            return None
        return self._history.generation if self._history.writable else -1

    def _invalidate_answers(self) -> None:
        self._invalid_before = next(self._gen)
        cutoff = self._invalid_before
        self._runner.call_soon(lambda: self._cancel_before(cutoff))

    def _cancel_before(self, cutoff: int) -> None:
        for gen, task in tuple(self._runs.items()):
            if gen < cutoff and not task.done():
                task.cancel()
                self.answer_stopped.emit(gen)

    async def _execute(
        self,
        gen: int,
        text: str = "",
        source: str = "typed",
        speech_lag: float | None = None,
        image: bytes | None = None,
        explicit: bool = False,
        category: Category | None = None,
        history_generation: int | None = None,
    ) -> None:
        if self._paused or gen < self._invalid_before:
            return
        current = asyncio.current_task()
        assert current is not None
        self._runs[gen] = current
        token = self._write_generation.set(history_generation)
        cost_token = self._tracker.begin_request() if self._tracker else None
        try:
            if category is not None:
                await self._run_corrected(gen, category)
            else:
                await self._run(gen, text, source, speech_lag, image, explicit)
        finally:
            self._runs.pop(gen, None)
            self._write_generation.reset(token)
            if self._tracker is not None and cost_token is not None:
                self._tracker.end_request(cost_token)

    def _stop_active(self) -> None:
        task = self._tasks.get(self._active_source)
        if task is not None and not task.done():
            gen = self._active_gen
            self._stopped_gen = gen
            task.cancel()
            self.answer_stopped.emit(gen)

    async def _run(
        self,
        gen: int,
        text: str,
        source: str,
        speech_lag: float | None = None,
        image: bytes | None = None,
        explicit: bool = False,
    ) -> None:
        asked_at = time.time()  # history order follows when the question was asked
        if self._tracker is not None and self._tracker.budget_state().blocked:
            await self._answer(
                gen, text, source, self._jev.manual_decision(Category.GENERAL), asked_at
            )
            return
        try:
            decision = await self._jev.decide(text)
        except Exception as e:
            if self._paused or gen < self._invalid_before:
                return
            log.exception("Classification failed")
            self._commit(gen, text, source)
            self.answer_failed.emit(gen, friendly_error(e))
            self._save(text, source, None, asked_at, status="error", error=friendly_error(e))
            return
        if self._paused or gen < self._invalid_before:
            return
        if not decision.should_answer and source.startswith("voice") and not explicit:
            self.question_ignored.emit(text)
            return
        if not decision.should_answer and (source == "typed" or explicit):
            # Typed text and the Answer button are explicit requests, even when the text isn't
            # phrased as a question.
            decision = self._jev.manual_decision(Category.GENERAL)
        if gen < self._committed_gen.get(source, 0):  # a newer same-source question is answering
            return
        self._last_question, self._last_source, self._last_decision = text, source, decision
        self._last_image = image
        await self._answer(gen, text, source, decision, asked_at, speech_lag, image=image)

    async def _run_corrected(self, gen: int, category: Category) -> None:
        """Redo the most recent question with a user-picked specialist (see correct_category)."""
        if self._last_question is None:
            return
        text, source = self._last_question, self._last_source
        original = self._last_decision.category if self._last_decision else None
        decision = self._jev.manual_decision(category)
        if original is not None and original != category:
            self.category_corrected.emit(text, original.value, category.value)
        self._last_decision = decision
        corrected_from = original.value if original is not None and original != category else ""
        await self._answer(
            gen, text, source, decision, time.time(), None, corrected_from, image=self._last_image
        )

    async def _answer(
        self,
        gen: int,
        text: str,
        source: str,
        decision: Decision,
        asked_at: float,
        speech_lag: float | None = None,
        corrected_from: str = "",
        image: bytes | None = None,
    ) -> None:
        state = self._tracker.budget_state() if self._tracker else None
        if state is not None and state.blocked:
            self._commit(gen, text, source)
            msg = (
                f"Budget reached ({format_usd(state.spent_usd)} of "
                f"{format_usd(state.limit_usd or 0)} this {state.scope}). "
                "Raise BUDGET_USD or turn off BUDGET_HARD_STOP to continue."
            )
            self.answer_failed.emit(gen, msg)
            self._save(
                text,
                source,
                decision,
                asked_at,
                status="error",
                error=msg,
                corrected_from=corrected_from,
            )
            return
        prev_task = self._tasks.get(source)
        if prev_task is not None and not prev_task.done():
            prev_task.cancel()
        self._commit(gen, text, source)
        current = asyncio.current_task()
        assert current is not None
        self._tasks[source] = current
        self._active_gen = gen
        self._active_source = source
        self.busy_changed.emit(True)
        self.decision_ready.emit(gen, decision)
        start = time.monotonic()
        first: float | None = None
        route = ""
        parts: list[str] = []
        status: Status = "complete"
        error = ""
        try:
            async for ev in self._jev.ask(text, decision, image=image):
                if not isinstance(ev, RouteEvent):
                    continue
                if ev.reset:
                    first = None
                    parts.clear()
                    self.answer_reset.emit(gen)
                if ev.event.delta:
                    if first is None:
                        first = time.monotonic() - start
                    parts.append(ev.event.delta)
                    self.answer_delta.emit(gen, ev.event.delta)
                route = str(ev.route)
            if self._tracker is not None:
                self.answer_cost.emit(gen, self._request_cost())
            answer_text = "".join(parts)
            self.answer_flags.emit(gen, self._unverified_claims(decision, text, answer_text))
            total = time.monotonic() - start
            self.latency_ready.emit(
                gen,
                {
                    "speech_to_question_s": speech_lag,  # transcription + question merging
                    "question_to_first_token_s": first,  # classification + model
                    "total_s": total,
                },
            )
            self.answer_finished.emit(gen, route, first or 0.0, total)
        except asyncio.CancelledError:
            status = "stopped" if self._stopped_gen == gen else "interrupted"
            raise
        except Exception as e:
            log.warning("Answer failed: %s", e)
            status, error = "error", friendly_error(e)
            self.answer_failed.emit(gen, error)
        finally:
            self._save(
                text,
                source,
                decision,
                asked_at,
                answer="".join(parts),
                status=status,
                route=route,
                first_token_s=first,
                total_s=time.monotonic() - start,
                cost=self._request_cost(),
                error=error,
                corrected_from=corrected_from,
            )
            if self._tasks.get(source) is current:
                self._tasks.pop(source, None)
            if self._active_gen == gen:
                remaining = [
                    (self._committed_gen[key], key)
                    for key, task in self._tasks.items()
                    if not task.done()
                ]
                self._active_gen, self._active_source = max(remaining, default=(0, ""))
                if not remaining:
                    self.busy_changed.emit(False)

    def _unverified_claims(self, decision: Decision, question: str, answer: str) -> list[str]:
        if decision.category.value not in CLAIM_PRONE:
            return []
        config = getattr(self._jev, "config", None)  # duck-typed: JevLike doesn't declare it
        profile = getattr(config, "profile", None) or ""
        return find_unverified_claims(answer, f"{profile}\n{question}")

    def _request_cost(self) -> float | None:
        return None if self._tracker is None else self._tracker.request_cost()

    def _save(
        self,
        text: str,
        source: str,
        decision: Decision | None,
        asked_at: float,
        answer: str = "",
        status: Status = "complete",
        route: str = "",
        first_token_s: float | None = None,
        total_s: float | None = None,
        cost: float | None = None,
        error: str = "",
        corrected_from: str = "",
    ) -> None:
        if self._history is None:
            return
        try:
            self._history.add_turn(
                TurnRecord(
                    question=text,
                    answer=answer.strip(),
                    source=source,
                    status=status,
                    category=decision.category.value if decision else "",
                    agent=decision.agent if decision else "",
                    tier=decision.tier if decision else "",
                    confidence=decision.confidence if decision else None,
                    decision_source=decision.source if decision else "",
                    route=route,
                    first_token_s=first_token_s,
                    total_s=total_s,
                    cost_usd=cost,
                    error=error,
                    asked_at=asked_at,
                    corrected_from=corrected_from,
                ),
                expected_generation=self._write_generation.get(),
            )
        except Exception:
            log.exception("Could not save history")  # never break answering over storage

    def _commit(self, gen: int, text: str, source: str) -> None:
        self._committed_gen[source] = max(self._committed_gen.get(source, 0), gen)
        self.question_started.emit(gen, text, source)
