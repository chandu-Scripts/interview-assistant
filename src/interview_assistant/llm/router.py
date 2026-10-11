import asyncio
import logging
import time
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass, field, replace

from interview_assistant.llm.base import LLMProvider
from interview_assistant.llm.types import (
    ErrorKind,
    LLMError,
    LLMRequest,
    LLMResult,
    StreamEvent,
    Usage,
)

log = logging.getLogger(__name__)

Clock = Callable[[], float]


@dataclass(frozen=True)
class Route:
    provider: str
    model: str

    def __str__(self) -> str:
        return f"{self.provider}:{self.model}"


@dataclass(frozen=True)
class RouteEvent:
    """A streamed event annotated with the route that produced it.

    reset=True means an earlier attempt already showed text and failed: the consumer must discard
    what it displayed so far; the answer restarts from this route.
    """

    event: StreamEvent
    route: Route
    attempt: int
    reset: bool = False


@dataclass
class AttemptRecord:
    route: Route
    attempt: int
    ok: bool
    latency_s: float
    first_token_s: float | None = None
    usage: Usage = field(default_factory=Usage)
    error: LLMError | None = None
    tag: str | None = None  # caller label for cost breakdowns, e.g. an agent name
    input_chars: int = 0  # request size, to estimate cost of attempts that died mid-stream
    output_chars: int = 0  # text received before the attempt ended
    cancelled: bool = False  # the consumer abandoned the stream


class AllRoutesFailed(RuntimeError):
    def __init__(self, errors: list[LLMError]):
        super().__init__("all routes failed: " + "; ".join(str(e) for e in errors))
        self.errors = errors


class NoRoutesAvailable(RuntimeError):
    pass


@dataclass
class RouterConfig:
    chains: dict[str, list[Route]]  # tier -> ordered preference
    first_token_timeout_s: float = 10.0
    stall_timeout_s: float = 20.0  # max silence between events once streaming
    failure_threshold: int = 2  # consecutive failures before a provider's circuit opens
    cooldowns_s: Mapping[ErrorKind, float] = field(
        default_factory=lambda: {
            ErrorKind.AUTH: 600.0,  # a bad key will not fix itself quickly
            ErrorKind.RATE_LIMIT: 60.0,
            ErrorKind.BAD_REQUEST: 30.0,
        }
    )
    default_cooldown_s: float = 30.0


class _Health:
    """Per-provider circuit breaker. An open circuit only demotes a provider to last resort."""

    def __init__(self, cfg: RouterConfig, clock: Clock) -> None:
        self._cfg = cfg
        self._clock = clock
        self._fails: dict[str, int] = {}
        self._open_until: dict[str, float] = {}

    def is_open(self, provider: str) -> bool:
        return self._open_until.get(provider, 0.0) > self._clock()

    def success(self, provider: str) -> None:
        self._fails[provider] = 0
        self._open_until.pop(provider, None)

    def failure(self, provider: str, kind: ErrorKind) -> None:
        n = self._fails.get(provider, 0) + 1
        self._fails[provider] = n
        # Auth failures open immediately: retrying with the same key is pointless.
        if n >= self._cfg.failure_threshold or kind is ErrorKind.AUTH:
            cd = self._cfg.cooldowns_s.get(kind, self._cfg.default_cooldown_s)
            self._open_until[provider] = self._clock() + cd


@dataclass
class _AttemptState:
    route: Route
    attempt: int
    start: float
    first_token: float | None = None
    usage: Usage = field(default_factory=Usage)
    out_chars: int = 0

    def finish(
        self,
        now: float,
        ok: bool,
        tag: str | None,
        in_chars: int,
        error: LLMError | None = None,
        cancelled: bool = False,
    ) -> AttemptRecord:
        return AttemptRecord(
            self.route,
            self.attempt,
            ok,
            now - self.start,
            self.first_token,
            self.usage,
            error,
            tag,
            in_chars,
            self.out_chars,
            cancelled,
        )


class Router:
    """Chooses a provider/model chain for a tier and fails over when a route misbehaves.

    Fails over on: any LLMError, no first token within first_token_timeout_s, or a stall
    mid-stream. Cancellation is never swallowed.
    """

    def __init__(
        self,
        providers: Mapping[str, LLMProvider],
        config: RouterConfig,
        on_attempt: Callable[[AttemptRecord], None] | None = None,
        clock: Clock = time.monotonic,
    ) -> None:
        self._providers = providers
        self._cfg = config
        self._on_attempt = on_attempt
        self._clock = clock
        self._health = _Health(config, clock)

    def chain_for(self, tier: str, provider: str | None = None) -> list[Route]:
        """Usable routes in try-order: configured providers only, unhealthy ones last."""
        if tier not in self._cfg.chains:
            raise NoRoutesAvailable(f"unknown tier {tier!r}")
        routes = [r for r in self._cfg.chains[tier] if r.provider in self._providers]
        if provider is not None:
            routes = [r for r in routes if r.provider == provider]
        if not routes:
            raise NoRoutesAvailable(f"no configured provider for tier {tier!r}")
        healthy = [r for r in routes if not self._health.is_open(r.provider)]
        tripped = [r for r in routes if self._health.is_open(r.provider)]
        return healthy + tripped

    async def stream(
        self,
        request: LLMRequest,
        tier: str = "fast",
        provider: str | None = None,
        tag: str | None = None,
        preferred: Route | None = None,
    ) -> AsyncIterator[RouteEvent]:
        if preferred is not None:
            if preferred.provider not in self._providers or not any(
                preferred in routes for routes in self._cfg.chains.values()
            ):
                raise NoRoutesAvailable(f"selected model is no longer configured: {preferred}")
            if provider is not None and preferred.provider != provider:
                raise NoRoutesAvailable("selected model does not match the requested provider")
        try:
            chain = self.chain_for(tier, provider)
        except NoRoutesAvailable:
            if preferred is None or tier not in self._cfg.chains:
                raise
            chain = []
        if preferred is not None:
            chain = [preferred] + [route for route in chain if route != preferred]
            chain.sort(key=lambda route: self._health.is_open(route.provider))
        in_chars = len(request.system or "") + sum(len(m.content) for m in request.messages)
        errors: list[LLMError] = []
        showed_text = False
        for attempt, route in enumerate(chain):
            reset = showed_text and attempt > 0
            if reset:
                yield RouteEvent(StreamEvent(), route, attempt, reset=True)
                showed_text = False
            inner = self._providers[route.provider].stream(replace(request, model=route.model))
            att = _AttemptState(route, attempt, self._clock())
            try:
                while True:
                    timeout = (
                        self._cfg.first_token_timeout_s
                        if att.first_token is None
                        else self._cfg.stall_timeout_s
                    )
                    try:
                        ev = await asyncio.wait_for(anext(inner), timeout)
                    except StopAsyncIteration:
                        break
                    except TimeoutError:
                        what = "first token" if att.first_token is None else "next chunk"
                        raise LLMError(
                            route.provider, ErrorKind.TIMEOUT, f"no {what} within {timeout:g}s"
                        ) from None
                    if ev.delta and att.first_token is None:
                        att.first_token = self._clock() - att.start
                    if ev.delta:
                        showed_text = True
                        att.out_chars += len(ev.delta)
                    if ev.usage:
                        att.usage = ev.usage
                    yield RouteEvent(ev, route, attempt)
            except LLMError as e:
                log.warning("Route %s failed: %s", route, e)
                errors.append(e)
                self._health.failure(route.provider, e.kind)
                self._record(att.finish(self._clock(), False, tag, in_chars, error=e))
                continue
            except (asyncio.CancelledError, GeneratorExit):
                # Interrupted by the caller: tokens were still consumed, so it must be billed.
                self._record(att.finish(self._clock(), False, tag, in_chars, cancelled=True))
                raise
            finally:
                await inner.aclose()
            self._health.success(route.provider)
            self._record(att.finish(self._clock(), True, tag, in_chars))
            return
        raise AllRoutesFailed(errors)

    def _record(self, rec: AttemptRecord) -> None:
        if self._on_attempt:
            try:
                self._on_attempt(rec)
            except Exception:
                log.exception("on_attempt callback failed")


@dataclass
class RoutedResult(LLMResult):
    attempts: int = 1


async def collect_routed(
    router: Router,
    request: LLMRequest,
    tier: str = "fast",
    provider: str | None = None,
    tag: str | None = None,
) -> RoutedResult:
    """Consume a routed stream into one result (text is reset when the router restarts)."""
    res = RoutedResult()
    parts: list[str] = []
    async for re in router.stream(request, tier, provider, tag):
        if re.reset:
            parts.clear()
        parts.append(re.event.delta)
        if re.event.usage:
            res.usage = re.event.usage
        if re.event.finish_reason:
            res.finish_reason = re.event.finish_reason
        res.provider, res.model, res.attempts = re.route.provider, re.route.model, re.attempt + 1
    res.text = "".join(parts)
    return res
