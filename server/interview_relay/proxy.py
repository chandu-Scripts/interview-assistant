"""Logic for the LLM proxy (/v1/chat/completions is wired in app.py; this module is the part
worth unit-testing in isolation).

A friend's app sends the same request shape interview_assistant.llm.openai_llm.OpenAIProvider
already builds - just pointed at this server with the friend's relay token instead of an OpenAI
key, and "model" set to the literal string "fast" or "smart" instead of a real model name. Nothing
here trusts the client for anything consequential: the real model, the max_completion_tokens
ceiling, and whether the friend can afford this request are all decided from server-side state.

Reuses interview_assistant.llm.sse (SSE parsing) and interview_assistant.cost.pricing (the exact
same $/token rates the desktop app shows) so cost accounting never drifts between the two. When
this is packaged for deployment, install interview-assistant with `--no-deps` (plus httpx) so this
server never pulls in the desktop-only PySide6/faster-whisper/pyaudiowpatch dependencies - nothing
imported here needs them.
"""

import json
import logging
import time
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

import httpx
from fastapi import HTTPException
from interview_assistant.cost.pricing import PriceBook
from interview_assistant.llm.sse import iter_sse

from interview_relay.config import RelaySettings
from interview_relay.db import Friend, RelayDB

log = logging.getLogger(__name__)

OPENAI_URL = "https://api.openai.com/v1/chat/completions"
CHARS_PER_TOKEN = 4  # same rough fallback estimate interview_assistant.cost.tracker uses
_PRICES = PriceBook()


def authenticate(db: RelayDB, authorization: str | None) -> Friend:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    friend = db.get_friend_by_token(authorization.removeprefix("Bearer ").strip())
    if friend is None or not friend.active:
        raise HTTPException(status_code=401, detail="Invalid or revoked access token")
    return friend


def start_of_scope(scope: str, now: float) -> float:
    dt = datetime.fromtimestamp(now)
    if scope == "month":
        return dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp()
    return dt.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()  # day, and default


def check_budget(db: RelayDB, friend: Friend, now: float) -> None:
    spent = db.spend_since(friend.id, start_of_scope(friend.budget_scope, now))
    if spent >= friend.budget_usd:
        raise HTTPException(
            status_code=402,
            detail=f"Budget reached (${spent:.2f} of ${friend.budget_usd:.2f} this "
            f"{friend.budget_scope}). Ask the owner to raise it, or wait for the next period.",
        )


def sanitize_body(settings: RelaySettings, body: dict[str, Any]) -> dict[str, Any]:
    """The request actually sent to OpenAI, built fresh - never the client's body forwarded
    as-is. `model` must be the literal tier name; everything consequential is decided here."""
    tier = body.get("model")
    if tier == "fast":
        model = settings.model_fast
    elif tier == "smart":
        model = settings.model_smart
    else:
        raise HTTPException(status_code=400, detail="model must be 'fast' or 'smart'")
    messages = body.get("messages")
    if not isinstance(messages, list) or not messages:
        raise HTTPException(status_code=400, detail="messages must be a non-empty list")
    max_tokens = settings.max_tokens_ceiling
    requested = body.get("max_completion_tokens")
    if isinstance(requested, int) and 0 < requested < max_tokens:
        max_tokens = requested
    out: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": True,
        "stream_options": {"include_usage": True},
        "max_completion_tokens": max_tokens,
    }
    temperature = body.get("temperature")
    if isinstance(temperature, int | float):
        out["temperature"] = temperature
    return out


def _message_chars(messages: list[Any]) -> int:
    return sum(len(m.get("content", "")) for m in messages if isinstance(m, dict))


async def open_upstream(
    http_client: httpx.AsyncClient, settings: RelaySettings, outgoing: dict[str, Any]
) -> httpx.Response:
    """Starts the real OpenAI request. Raises HTTPException for anything that should stop the
    friend's request before a single byte is streamed back to them."""
    if not settings.openai_api_key:
        raise HTTPException(status_code=503, detail="Relay is not configured with an OpenAI key")
    request = http_client.build_request(
        "POST",
        OPENAI_URL,
        headers={"Authorization": f"Bearer {settings.openai_api_key}"},
        json=outgoing,
    )
    try:
        upstream = await http_client.send(request, stream=True)
    except httpx.HTTPError as e:
        raise HTTPException(
            status_code=502, detail=f"Could not reach OpenAI: {type(e).__name__}"
        ) from e
    if upstream.status_code >= 400:
        detail = (await upstream.aread())[:300].decode("utf-8", "replace")
        await upstream.aclose()
        raise HTTPException(
            status_code=502, detail=f"OpenAI returned {upstream.status_code}: {detail}"
        )
    return upstream


async def stream_and_bill(
    db: RelayDB, friend: Friend, model: str, input_chars: int, upstream: httpx.Response
) -> AsyncIterator[str]:
    """Re-emits the upstream SSE stream to the friend's app unchanged (line-for-line, payload
    untouched), while separately watching for the trailing usage chunk to bill the right amount.
    Billing happens exactly once per request, after the stream ends, from real token counts
    whenever OpenAI reports them - never from anything the client claims."""
    usage: tuple[int, int] | None = None
    output_chars = 0
    try:
        async for _event, data in iter_sse(upstream):
            yield f"data: {data}\n\n"
            if data.strip() == "[DONE]":
                break
            try:
                obj = json.loads(data)
            except ValueError:
                continue
            if not isinstance(obj, dict):
                continue
            if u := obj.get("usage"):
                usage = (u.get("prompt_tokens", 0), u.get("completion_tokens", 0))
            delta = (((obj.get("choices") or [{}])[0]).get("delta") or {}).get("content")
            if delta:
                output_chars += len(delta)
    finally:
        await upstream.aclose()
        if usage is None:
            # Died mid-stream before the usage chunk arrived (client disconnect, upstream drop):
            # still bill something close, the same char/4 estimate the desktop app falls back to.
            usage = (
                max(1, input_chars // CHARS_PER_TOKEN),
                max(1, output_chars // CHARS_PER_TOKEN),
            )
            log.warning(
                "No usage reported for friend %s; billing an estimate from length", friend.id
            )
        cost = _PRICES.llm_cost("openai", model, usage[0], usage[1])
        if cost is None:
            log.error(
                "No known price for model %r - billed $0 for friend %s! Fix the model env vars.",
                model,
                friend.id,
            )
            cost = 0.0
        db.add_spend(friend.id, cost)


async def handle_chat_completions(
    db: RelayDB,
    settings: RelaySettings,
    http_client: httpx.AsyncClient,
    friend: Friend,
    body: dict[str, Any],
) -> AsyncIterator[str]:
    """The full request path once authentication has already succeeded: sanitize -> budget check
    -> call OpenAI -> stream back while billing. Raises HTTPException for anything that must stop
    the request before any bytes reach the friend (bad body, budget exceeded, OpenAI unreachable).
    """
    outgoing = sanitize_body(settings, body)
    check_budget(db, friend, time.time())
    upstream = await open_upstream(http_client, settings, outgoing)
    input_chars = _message_chars(outgoing["messages"])
    return stream_and_bill(db, friend, outgoing["model"], input_chars, upstream)
