import asyncio

import pytest

from interview_assistant.config.settings import Settings
from interview_assistant.llm.router import (
    AllRoutesFailed,
    AttemptRecord,
    NoRoutesAvailable,
    Route,
    Router,
    RouterConfig,
    collect_routed,
)
from interview_assistant.llm.router_factory import build_router, default_chains, parse_chain
from interview_assistant.llm.types import (
    ErrorKind,
    LLMError,
    LLMRequest,
    Message,
    StreamEvent,
    Usage,
)

REQ = LLMRequest([Message("user", "Hi")])


async def test_preferred_model_crosses_tiers_and_keeps_fallback_and_accounting():
    a = Script("a", ["fallback"])
    b = Script("b", ["partial", err("b")])
    records = []
    router = Router(
        {"a": a, "b": b},
        RouterConfig(chains={"fast": [Route("a", "fast")], "smart": [Route("b", "smart")]}),
        records.append,
    )
    events = [e async for e in router.stream(REQ, "fast", preferred=Route("b", "smart"))]
    assert b.calls == ["smart"] and a.calls == ["fast"]
    assert any(e.reset for e in events)
    assert [r.ok for r in records] == [False, True]
    assert events[-1].route == Route("a", "fast")


async def test_preferred_model_works_when_default_tier_has_no_available_provider():
    b = Script("b", ["answer"])
    router = Router(
        {"b": b},
        RouterConfig(chains={"fast": [Route("a", "fast")], "smart": [Route("b", "smart")]}),
    )
    events = [e async for e in router.stream(REQ, "fast", preferred=Route("b", "smart"))]
    assert events[-1].route == Route("b", "smart")
    with pytest.raises(NoRoutesAvailable, match="no longer configured"):
        _ = [e async for e in router.stream(REQ, preferred=Route("b", "removed"))]


class Script:
    """Scripted provider; steps: str delta, ("sleep", s), or an LLMError to raise."""

    def __init__(self, name, steps, model="m"):
        self.name, self.default_model, self.steps = name, model, steps
        self.calls: list[str | None] = []
        self.closed = 0

    async def stream(self, request):
        self.calls.append(request.model)
        try:
            for s in self.steps:
                if isinstance(s, LLMError):
                    raise s
                if isinstance(s, tuple):
                    await asyncio.sleep(s[1])
                    continue
                yield StreamEvent(delta=s)
            yield StreamEvent(usage=Usage(5, 2), finish_reason="stop")
        finally:
            self.closed += 1


def err(p, kind=ErrorKind.SERVER):
    return LLMError(p, kind, "x")


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def make(providers, chain=None, clock=None, **cfg):
    chain = chain or [Route(p.name, f"{p.name}-model") for p in providers]
    config = RouterConfig(chains={"fast": chain, "smart": list(reversed(chain))}, **cfg)
    records: list[AttemptRecord] = []
    kw = {"clock": clock} if clock else {}
    router = Router({p.name: p for p in providers}, config, records.append, **kw)
    return router, records


async def test_happy_path_uses_first_route_and_sets_model():
    a, b = Script("a", ["He", "llo"]), Script("b", ["nope"])
    r, recs = make([a, b])
    res = await collect_routed(r, REQ)
    assert (res.text, res.provider, res.model, res.attempts) == ("Hello", "a", "a-model", 1)
    assert res.usage == Usage(5, 2) and a.calls == ["a-model"] and b.calls == []
    assert len(recs) == 1 and recs[0].ok and recs[0].first_token_s is not None


async def test_failover_before_any_output_has_no_reset():
    a, b = Script("a", [err("a", ErrorKind.RATE_LIMIT)]), Script("b", ["ok"])
    r, recs = make([a, b])
    events = [e async for e in r.stream(REQ)]
    assert not any(e.reset for e in events)
    assert "".join(e.event.delta for e in events) == "ok"
    assert [x.ok for x in recs] == [False, True] and recs[0].error.kind is ErrorKind.RATE_LIMIT


async def test_midstream_failure_restarts_with_reset_marker():
    a, b = Script("a", ["par", "tial", err("a")]), Script("b", ["full ", "answer"])
    r, _ = make([a, b])
    events = [e async for e in r.stream(REQ)]
    assert [e.reset for e in events].count(True) == 1
    assert events[[e.reset for e in events].index(True)].route.provider == "b"
    res = await collect_routed(
        make([Script("a", ["par", err("a")]), Script("b", ["full"])])[0], REQ
    )
    assert res.text == "full" and res.attempts == 2  # partial text was discarded


async def test_all_routes_failed_collects_errors():
    a = Script("a", [err("a", ErrorKind.AUTH)])
    b = Script("b", [err("b", ErrorKind.SERVER)])
    r, recs = make([a, b])
    with pytest.raises(AllRoutesFailed) as ei:
        await collect_routed(r, REQ)
    assert [e.provider for e in ei.value.errors] == ["a", "b"] and len(recs) == 2


async def test_first_token_timeout_falls_over():
    a = Script("a", [("sleep", 5), "late"])
    b = Script("b", ["fast"])
    r, recs = make([a, b], first_token_timeout_s=0.1)
    res = await collect_routed(r, REQ)
    assert res.text == "fast" and res.provider == "b"
    assert recs[0].error.kind is ErrorKind.TIMEOUT
    assert a.closed == 1  # slow stream was cleaned up


async def test_stall_after_first_token_falls_over():
    a = Script("a", ["start", ("sleep", 5), "never"])
    b = Script("b", ["recovered"])
    r, _ = make([a, b], first_token_timeout_s=1, stall_timeout_s=0.1)
    res = await collect_routed(r, REQ)
    assert res.text == "recovered"


async def test_circuit_breaker_demotes_then_recovers():
    clk = Clock()
    a = Script("a", [err("a")])
    b = Script("b", ["ok"])
    r, _ = make([a, b], clock=clk, failure_threshold=2)
    await collect_routed(r, REQ)  # a fails (1)
    assert [x.provider for x in r.chain_for("fast")] == ["a", "b"]
    await collect_routed(r, REQ)  # a fails (2) -> open
    assert [x.provider for x in r.chain_for("fast")] == ["b", "a"]
    n = len(a.calls)
    await collect_routed(r, REQ)
    assert len(a.calls) == n  # skipped while open (b served first)
    clk.t += 31  # cooldown elapsed
    assert [x.provider for x in r.chain_for("fast")] == ["a", "b"]
    a.steps = ["healed"]
    assert (await collect_routed(r, REQ)).text == "healed"
    assert [x.provider for x in r.chain_for("fast")] == ["a", "b"]


async def test_auth_error_opens_circuit_immediately_and_long():
    clk = Clock()
    a, b = Script("a", [err("a", ErrorKind.AUTH)]), Script("b", ["ok"])
    r, _ = make([a, b], clock=clk)
    await collect_routed(r, REQ)
    assert [x.provider for x in r.chain_for("fast")] == ["b", "a"]
    clk.t += 300
    assert [x.provider for x in r.chain_for("fast")] == ["b", "a"]  # still cooling (600s)


async def test_all_open_still_tries_everything():
    a, b = Script("a", [err("a", ErrorKind.AUTH)]), Script("b", [err("b", ErrorKind.AUTH)])
    r, _ = make([a, b])
    with pytest.raises(AllRoutesFailed):
        await collect_routed(r, REQ)
    b.steps = ["back"]
    assert (await collect_routed(r, REQ)).text == "back"  # open circuits are only demoted


async def test_tier_and_provider_pinning_and_unconfigured_filtered():
    a, b = Script("a", ["A"]), Script("b", ["B"])
    chain = [Route("ghost", "x"), Route("a", "am"), Route("b", "bm")]
    r, _ = make([a, b], chain=chain)
    assert (await collect_routed(r, REQ, "fast")).provider == "a"  # ghost has no provider
    assert (await collect_routed(r, REQ, "smart")).provider == "b"  # smart chain is reversed
    assert (await collect_routed(r, REQ, "fast", provider="b")).provider == "b"
    with pytest.raises(NoRoutesAvailable):
        r.chain_for("nope")
    with pytest.raises(NoRoutesAvailable):
        r.chain_for("fast", provider="zzz")


async def test_cancellation_propagates_and_closes_stream():
    a = Script("a", ["x", ("sleep", 10)])
    r, _ = make([a, Script("b", ["y"])], first_token_timeout_s=5, stall_timeout_s=30)

    async def consume():
        async for _ in r.stream(REQ):
            pass

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert a.closed == 1


async def test_callback_exception_does_not_break_stream():
    a = Script("a", ["ok"])
    config = RouterConfig(chains={"fast": [Route("a", "m")]})

    def boom(_):
        raise RuntimeError("cb")

    r = Router({"a": a}, config, boom)
    assert (await collect_routed(r, REQ)).text == "ok"


# ---------------- config / factory ----------------
def test_parse_chain():
    assert parse_chain(" OpenAI:gpt-x , gemini:g:1 ") == [
        Route("openai", "gpt-x"),
        Route("gemini", "g:1"),
    ]
    with pytest.raises(ValueError):
        parse_chain("nomodel")


def test_default_and_overridden_chains(monkeypatch):
    for k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    s = Settings(_env_file=None)
    ch = default_chains(s)
    assert ch["fast"][0].provider == "openai" and ch["smart"][0].provider == "anthropic"
    s2 = Settings(_env_file=None, router_fast="gemini:g1")
    assert default_chains(s2)["fast"] == [Route("gemini", "g1")]


def test_default_chains_use_relay_when_shared_access_is_connected():
    s = Settings(_env_file=None, relay_url="https://relay.example", relay_token="ika_tok")
    ch = default_chains(s)
    assert ch["fast"] == [Route("relay", "fast")]
    assert ch["smart"] == [Route("relay", "smart")]
    # an explicit override still wins over the relay default
    s2 = Settings(
        _env_file=None,
        relay_url="https://relay.example",
        relay_token="ika_tok",
        router_fast="gemini:g1",
    )
    assert default_chains(s2)["fast"] == [Route("gemini", "g1")]


async def test_build_router_uses_only_configured_providers(monkeypatch):
    for k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    s = Settings(_env_file=None, gemini_api_key="g")
    r = build_router(s)
    assert [x.provider for x in r.chain_for("fast")] == ["gemini"]
    with pytest.raises(NoRoutesAvailable):
        build_router(Settings(_env_file=None)).chain_for("fast")


async def test_build_router_end_to_end_through_a_relay_connection(monkeypatch):
    """Factory + router_factory together: a friend with no provider keys of their own, only a
    relay connection, gets a working router whose routes are literally "relay:fast"/"relay:smart"
    - the same request/stream path as a direct provider, with build_providers doing the only
    relay-specific wiring (an OpenAIProvider pointed at the relay's base_url)."""
    for k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    s = Settings(_env_file=None, relay_url="https://relay.example", relay_token="ika_tok")
    r = build_router(s)
    assert [x.provider for x in r.chain_for("fast")] == ["relay"]
    assert [x.provider for x in r.chain_for("smart")] == ["relay"]
