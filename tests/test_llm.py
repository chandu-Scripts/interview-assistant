import asyncio
import json
import os

import httpx
import pytest

from interview_assistant.config.settings import Settings
from interview_assistant.llm.anthropic_llm import AnthropicProvider
from interview_assistant.llm.base import collect
from interview_assistant.llm.factory import build_providers
from interview_assistant.llm.gemini_llm import GeminiProvider
from interview_assistant.llm.openai_llm import OpenAIProvider
from interview_assistant.llm.types import ErrorKind, LLMError, LLMRequest, Message, Usage

REQ = LLMRequest([Message("user", "Hi")], system="Be brief.", max_tokens=50)


def client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def sse(*events: tuple[str | None, object]) -> httpx.Response:
    out = ""
    for ev, data in events:
        if ev:
            out += f"event: {ev}\n"
        out += f"data: {data if isinstance(data, str) else json.dumps(data)}\n\n"
    return httpx.Response(200, content=out.encode(), headers={"content-type": "text/event-stream"})


# ---------------- OpenAI ----------------
def openai_ok(_req):
    return sse(
        (None, {"choices": [{"delta": {"role": "assistant"}}]}),
        (None, {"choices": [{"delta": {"content": "Hel"}}]}),
        (None, {"choices": [{"delta": {"content": "lo"}}]}),
        (None, {"choices": [{"delta": {}, "finish_reason": "stop"}]}),
        (None, {"choices": [], "usage": {"prompt_tokens": 11, "completion_tokens": 2}}),
        (None, "[DONE]"),
    )


async def test_openai_stream_and_request():
    seen = {}

    def h(req):
        seen["auth"] = req.headers["authorization"]
        seen["body"] = json.loads(req.content)
        return openai_ok(req)

    p = OpenAIProvider("sk-test", client=client(h))
    deltas = [e.delta async for e in p.stream(REQ) if e.delta]
    assert deltas == ["Hel", "lo"]
    assert seen["auth"] == "Bearer sk-test"
    b = seen["body"]
    assert b["stream"] is True and b["max_completion_tokens"] == 50 and "temperature" not in b
    assert b["messages"][0] == {"role": "system", "content": "Be brief."}
    assert b["messages"][1] == {"role": "user", "content": "Hi"}


async def test_openai_collect_usage():
    p = OpenAIProvider("k", client=client(openai_ok))
    r = await collect(p, REQ)
    assert (r.text, r.usage, r.finish_reason) == ("Hello", Usage(11, 2), "stop")
    assert r.provider == "openai" and r.model == "gpt-4o-mini"


# ---------------- Anthropic ----------------
def anthropic_ok(_req):
    return sse(
        ("message_start", {"type": "message_start", "message": {"usage": {"input_tokens": 9}}}),
        ("content_block_start", {"type": "content_block_start", "index": 0}),
        ("ping", {"type": "ping"}),
        (
            "content_block_delta",
            {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Hey"}},
        ),
        (
            "content_block_delta",
            {"type": "content_block_delta", "delta": {"type": "text_delta", "text": " you"}},
        ),
        (
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn"},
                "usage": {"output_tokens": 4},
            },
        ),
        ("message_stop", {"type": "message_stop"}),
    )


async def test_anthropic_stream_and_request():
    seen = {}

    def h(req):
        seen["headers"] = req.headers
        seen["body"] = json.loads(req.content)
        return anthropic_ok(req)

    p = AnthropicProvider("ak", client=client(h))
    r = await collect(p, REQ)
    assert (r.text, r.usage, r.finish_reason) == ("Hey you", Usage(9, 4), "end_turn")
    assert seen["headers"]["x-api-key"] == "ak"
    assert seen["headers"]["anthropic-version"] == "2023-06-01"
    assert seen["body"]["system"] == "Be brief." and seen["body"]["max_tokens"] == 50
    assert seen["body"]["messages"] == [{"role": "user", "content": "Hi"}]


async def test_anthropic_midstream_error_event():
    def h(_req):
        return sse(
            (
                "content_block_delta",
                {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "x"}},
            ),
            ("error", {"type": "error", "error": {"type": "overloaded_error", "message": "busy"}}),
        )

    p = AnthropicProvider("ak", client=client(h))
    with pytest.raises(LLMError) as ei:
        await collect(p, REQ)
    assert ei.value.kind is ErrorKind.SERVER and ei.value.retryable


# ---------------- Gemini ----------------
def gemini_ok(_req):
    return sse(
        (None, {"candidates": [{"content": {"parts": [{"text": "Bon"}]}}]}),
        (
            None,
            {
                "candidates": [{"content": {"parts": [{"text": "jour"}]}, "finishReason": "STOP"}],
                "usageMetadata": {
                    "promptTokenCount": 7,
                    "candidatesTokenCount": 3,
                    "thoughtsTokenCount": 5,
                },
            },
        ),
    )


async def test_gemini_stream_and_request():
    seen = {}

    def h(req):
        seen["url"] = str(req.url)
        seen["key"] = req.headers["x-goog-api-key"]
        seen["body"] = json.loads(req.content)
        return gemini_ok(req)

    p = GeminiProvider("gk", client=client(h))
    multi = LLMRequest(
        [Message("user", "a"), Message("assistant", "b"), Message("user", "c")], system="S"
    )
    r = await collect(p, multi)
    assert (r.text, r.usage, r.finish_reason) == ("Bonjour", Usage(7, 8), "STOP")
    assert "gemini-flash-latest:streamGenerateContent?alt=sse" in seen["url"]
    assert "gk" not in seen["url"] and seen["key"] == "gk"  # key in header, never in URL
    roles = [c["role"] for c in seen["body"]["contents"]]
    assert roles == ["user", "model", "user"]
    assert seen["body"]["systemInstruction"]["parts"][0]["text"] == "S"


# ---------------- errors (all providers) ----------------
PROVIDERS = [OpenAIProvider, AnthropicProvider, GeminiProvider]


@pytest.mark.parametrize("cls", PROVIDERS)
@pytest.mark.parametrize(
    ("status", "kind", "retryable"),
    [
        (401, ErrorKind.AUTH, False),
        (400, ErrorKind.BAD_REQUEST, False),
        (429, ErrorKind.RATE_LIMIT, True),
        (500, ErrorKind.SERVER, True),
        (529, ErrorKind.SERVER, True),
    ],
)
async def test_http_error_mapping(cls, status, kind, retryable):
    p = cls("secret-key", client=client(lambda r: httpx.Response(status, json={"error": "nope"})))
    with pytest.raises(LLMError) as ei:
        await collect(p, REQ)
    assert ei.value.kind is kind and ei.value.retryable is retryable
    assert ei.value.status == status and "secret-key" not in str(ei.value)


@pytest.mark.parametrize("cls", PROVIDERS)
async def test_timeout_and_network_errors(cls):
    def timeout(_r):
        raise httpx.ReadTimeout("slow")

    def down(_r):
        raise httpx.ConnectError("dns")

    with pytest.raises(LLMError) as e1:
        await collect(cls("k", client=client(timeout)), REQ)
    assert e1.value.kind is ErrorKind.TIMEOUT and e1.value.retryable
    with pytest.raises(LLMError) as e2:
        await collect(cls("k", client=client(down)), REQ)
    assert e2.value.kind is ErrorKind.NETWORK


@pytest.mark.parametrize("cls", PROVIDERS)
async def test_garbage_stream_is_parse_error(cls):
    p = cls("k", client=client(lambda r: sse((None, "{not json"))))
    with pytest.raises(LLMError) as ei:
        await collect(p, REQ)
    assert ei.value.kind is ErrorKind.PARSE and not ei.value.retryable


# ---------------- streaming behaviour ----------------
async def test_stream_is_incremental_and_cancellable():
    p = OpenAIProvider("k", client=client(openai_ok))
    it = p.stream(REQ)
    first = await anext(it)
    assert first.delta == "Hel"
    await it.aclose()  # consumer abandons the stream (new question arrived)

    async def consume():
        async for _ in OpenAIProvider("k", client=client(openai_ok)).stream(REQ):
            await asyncio.sleep(10)

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


# ---------------- factory / settings ----------------
def test_factory_only_configured_providers(monkeypatch):
    for k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    assert build_providers(Settings(_env_file=None)) == {}
    s = Settings(_env_file=None, openai_api_key="a", gemini_api_key="g", gemini_model="m")
    ps = build_providers(s)
    assert set(ps) == {"openai", "gemini"}
    assert ps["gemini"].default_model == "m"


def test_factory_registers_relay_provider_pointed_at_the_proxy_path(monkeypatch):
    for k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    s = Settings(_env_file=None, relay_url="https://relay.example/", relay_token="ika_tok")
    ps = build_providers(s)
    assert set(ps) == {"relay"}
    assert isinstance(ps["relay"], OpenAIProvider)
    assert ps["relay"]._url == "https://relay.example/v1/chat/completions"  # trailing slash ok


def test_factory_relay_needs_both_url_and_token(monkeypatch):
    for k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    assert build_providers(Settings(_env_file=None, relay_url="https://relay.example")) == {}
    assert build_providers(Settings(_env_file=None, relay_token="ika_tok")) == {}


# ---------------- live (opt-in: RUN_LIVE=1, needs the key) ----------------
@pytest.mark.skipif(
    not (os.environ.get("RUN_LIVE") and os.environ.get("GEMINI_API_KEY")),
    reason="live test: set RUN_LIVE=1 and GEMINI_API_KEY",
)
async def test_live_gemini():
    p = GeminiProvider(os.environ["GEMINI_API_KEY"])
    req = LLMRequest([Message("user", "Reply with exactly: pong")], max_tokens=200)
    chunks = [e async for e in p.stream(req)]
    text = "".join(e.delta for e in chunks)
    assert "pong" in text.lower()
    assert any(e.usage and e.usage.input_tokens > 0 for e in chunks)
