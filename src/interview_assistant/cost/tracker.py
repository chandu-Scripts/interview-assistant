import json
import logging
import threading
import time
import uuid
from collections import defaultdict
from collections.abc import Callable
from contextvars import ContextVar, Token
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal

from interview_assistant.cost.pricing import PriceBook
from interview_assistant.llm.router import AttemptRecord

log = logging.getLogger(__name__)

CHARS_PER_TOKEN = 4  # rough English estimate, used only when a provider never reported usage


@dataclass(frozen=True)
class UsageRecord:
    """One billable (or possibly billable) call. Metadata only - never prompt or answer text."""

    ts: float
    session: str
    kind: Literal["llm", "stt"]
    provider: str
    model: str
    tag: str  # agent/category for llm ("classify", "coding", ...), "" for stt
    input_tokens: int = 0
    output_tokens: int = 0
    audio_s: float = 0.0
    cost_usd: float | None = 0.0  # None = no known price for this model
    ok: bool = True
    estimated: bool = False  # tokens were estimated from text length (call ended before usage)
    cancelled: bool = False


@dataclass
class Summary:
    total_usd: float = 0.0
    calls: int = 0
    unpriced_calls: int = 0  # calls whose model has no price: total_usd is a lower bound
    input_tokens: int = 0
    output_tokens: int = 0
    audio_s: float = 0.0
    llm_usd: float = 0.0
    stt_usd: float = 0.0
    wasted_usd: float = 0.0  # spent on failed or cancelled attempts
    by_provider: dict[str, float] = field(default_factory=dict)
    by_model: dict[str, float] = field(default_factory=dict)
    by_tag: dict[str, float] = field(default_factory=dict)


class BudgetLevel(StrEnum):
    OK = "ok"
    WARN = "warn"
    EXCEEDED = "exceeded"


@dataclass(frozen=True)
class Budget:
    limit_usd: float
    scope: Literal["session", "day", "month"] = "session"
    hard_stop: bool = False  # block new LLM calls once exceeded
    warn_at: float = 0.8


@dataclass(frozen=True)
class BudgetState:
    level: BudgetLevel
    spent_usd: float
    limit_usd: float | None
    scope: str
    hard_stop: bool

    @property
    def blocked(self) -> bool:
        return self.level is BudgetLevel.EXCEEDED and self.hard_stop


class UsageStore:
    """Append-only JSONL usage log so day/month totals survive restarts."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def clear(self) -> None:
        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            log.exception("Could not delete usage log")

    def append(self, rec: UsageRecord) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(asdict(rec)) + "\n")
        except OSError:
            log.exception("Could not write usage log")

    def load_since(self, ts: float) -> list[UsageRecord]:
        out: list[UsageRecord] = []
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return out
        for line in lines:
            try:
                rec = UsageRecord(**json.loads(line))
            except (ValueError, TypeError):
                continue  # skip corrupt or foreign lines
            if rec.ts >= ts:
                out.append(rec)
        return out


def _start_of(scope: str, now: float) -> float:
    dt = datetime.fromtimestamp(now)
    if scope == "day":
        return dt.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    if scope == "month":
        return dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp()
    return 0.0


@dataclass
class RequestCost:
    session: str
    persist: bool
    persistence_generation: int
    total: float = 0.0
    unpriced: bool = False


class CostTracker:
    """Thread-safe usage accounting for LLM calls and cloud speech-to-text."""

    def __init__(
        self,
        prices: PriceBook | None = None,
        store: UsageStore | None = None,
        budget: Budget | None = None,
        on_change: Callable[[], None] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.prices = prices or PriceBook()
        self.budget = budget
        self._store = store
        self.on_change = on_change  # settable: the UI hooks in after construction
        self._persist = True  # False (incognito): keep in memory only, write nothing to disk
        self._persistence_generation = 0
        self._clock = clock
        self._lock = threading.Lock()
        self._session = uuid.uuid4().hex[:8]
        self._records: list[UsageRecord] = []  # everything this run + persisted history
        self._request: ContextVar[RequestCost | None] = ContextVar("request_cost", default=None)
        if store is not None:
            month_start = _start_of("month", clock())
            self._records = store.load_since(month_start)

    # ---- recording ----
    @property
    def persist(self) -> bool:
        return self._persist

    @persist.setter
    def persist(self, value: bool) -> None:
        with self._lock:
            if self._persist != value:
                self._persistence_generation += 1
            self._persist = value

    def begin_request(self) -> Token[RequestCost | None]:
        with self._lock:
            request = RequestCost(self._session, self.persist, self._persistence_generation)
        return self._request.set(request)

    def end_request(self, token: Token[RequestCost | None]) -> None:
        self._request.reset(token)

    def request_cost(self) -> float | None:
        request = self._request.get()
        return request.total if request is not None and not request.unpriced else None

    def wipe(self) -> None:
        """Forget all recorded usage, in memory and on disk."""
        with self._lock:
            self._records.clear()
            self._persistence_generation += 1
        if self._store is not None:
            self._store.clear()
        self._changed()

    def new_session(self) -> None:
        with self._lock:
            self._session = uuid.uuid4().hex[:8]
        self._changed()

    def record_attempt(self, a: AttemptRecord) -> None:
        """Router on_attempt callback."""
        usage, estimated = a.usage, False
        if usage.input_tokens == 0 and usage.output_tokens == 0 and a.output_chars > 0:
            # Died or was cancelled after the model began answering, before usage was reported.
            usage = type(usage)(a.input_chars // CHARS_PER_TOKEN, a.output_chars // CHARS_PER_TOKEN)
            estimated = True
        provider, model = a.route.provider, a.route.model
        self._add(
            UsageRecord(
                ts=self._clock(),
                session=self._session,
                kind="llm",
                provider=provider,
                model=model,
                tag=a.tag or "",
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cost_usd=self.prices.llm_cost(
                    provider, model, usage.input_tokens, usage.output_tokens
                ),
                ok=a.ok,
                estimated=estimated,
                cancelled=a.cancelled,
            )
        )

    def record_stt(self, provider: str, model: str, seconds: float) -> None:
        self._add(
            UsageRecord(
                ts=self._clock(),
                session=self._session,
                kind="stt",
                provider=provider,
                model=model,
                tag="",
                audio_s=seconds,
                cost_usd=self.prices.stt_cost(provider, model, seconds),
            )
        )

    def _add(self, rec: UsageRecord) -> None:
        request = self._request.get() if rec.kind == "llm" else None
        if request is not None:
            rec = replace(rec, session=request.session)
            request.total += rec.cost_usd or 0.0
            request.unpriced |= rec.cost_usd is None
        with self._lock:
            self._records.append(rec)
            may_persist = request is None or (
                request.persist and request.persistence_generation == self._persistence_generation
            )
            if self._store is not None and self.persist and may_persist:
                self._store.append(rec)
        self._changed()

    def _changed(self) -> None:
        if self.on_change is not None:
            try:
                self.on_change()
            except Exception:
                log.exception("on_change callback failed")

    # ---- queries ----
    def summary(self, scope: Literal["session", "day", "month", "all"] = "session") -> Summary:
        with self._lock:
            recs = list(self._records)
            session = self._session
        since = 0.0 if scope in ("session", "all") else _start_of(scope, self._clock())
        s = Summary()
        by_provider: dict[str, float] = defaultdict(float)
        by_model: dict[str, float] = defaultdict(float)
        by_tag: dict[str, float] = defaultdict(float)
        for r in recs:
            if r.ts < since or (scope == "session" and r.session != session):
                continue
            s.calls += 1
            s.input_tokens += r.input_tokens
            s.output_tokens += r.output_tokens
            s.audio_s += r.audio_s
            if r.cost_usd is None:
                s.unpriced_calls += 1
                continue
            s.total_usd += r.cost_usd
            if r.kind == "llm":
                s.llm_usd += r.cost_usd
            else:
                s.stt_usd += r.cost_usd
            if not r.ok or r.cancelled:
                s.wasted_usd += r.cost_usd
            by_provider[r.provider] += r.cost_usd
            by_model[f"{r.provider}:{r.model}"] += r.cost_usd
            if r.tag:
                by_tag[r.tag] += r.cost_usd
        s.by_provider, s.by_model, s.by_tag = dict(by_provider), dict(by_model), dict(by_tag)
        return s

    def total_usd(self) -> float:
        return self.summary("session").total_usd

    def budget_state(self) -> BudgetState:
        b = self.budget
        if b is None:
            return BudgetState(BudgetLevel.OK, self.total_usd(), None, "session", False)
        spent = self.summary(b.scope).total_usd
        if spent >= b.limit_usd:
            level = BudgetLevel.EXCEEDED
        elif spent >= b.limit_usd * b.warn_at:
            level = BudgetLevel.WARN
        else:
            level = BudgetLevel.OK
        return BudgetState(level, spent, b.limit_usd, b.scope, b.hard_stop)


def format_usd(v: float, unpriced: bool = False) -> str:
    text = f"${v:.5f}" if v < 0.01 else f"${v:.4f}" if v < 1 else f"${v:.2f}"
    return text + "+" if unpriced else text
