"""Authenticated streaming proxy with durable admission and conservative settlement."""

import asyncio
import json
import logging
import math
import sqlite3
from collections.abc import AsyncIterator
from typing import Any

import anyio
import httpx
from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from interview_assistant.cost.pricing import TokenPrice
from interview_assistant.llm.sse import iter_sse
from starlette.types import Receive, Scope, Send

from interview_relay.config import RelaySettings
from interview_relay.db import AdmissionError, Friend, RelayDB, Reservation
from interview_relay.input_limits import sanitize_body

log = logging.getLogger(__name__)
OPENAI_URL = "https://api.openai.com/v1/chat/completions"
MAX_REQUEST_SECONDS = 120


def authenticate(db: RelayDB, authorization: str | None) -> Friend:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    friend = db.get_friend_by_token(authorization.removeprefix("Bearer ").strip())
    if friend is None or not friend.active:
        raise HTTPException(status_code=401, detail="Invalid or revoked access token")
    return friend


class UnsentRequest(Exception):
    """A connection could not be made; no request bytes reached the API."""


class RejectedRequest(Exception):
    """An explicit upstream 4xx rejected generation."""

    def __init__(self, status: int):
        self.status = status


async def open_upstream(
    http_client: httpx.AsyncClient, settings: RelaySettings, outgoing: dict[str, Any]
) -> httpx.Response:
    request = http_client.build_request(
        "POST",
        OPENAI_URL,
        headers={"Authorization": f"Bearer {settings.openai_api_key}"},
        json=outgoing,
    )
    try:
        upstream = await http_client.send(request, stream=True)
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as e:
        raise UnsentRequest from e
    if 400 <= upstream.status_code < 500:
        try:
            with anyio.CancelScope(shield=True):
                with anyio.move_on_after(5):
                    await upstream.aclose()
        finally:
            raise RejectedRequest(upstream.status_code)
    if upstream.status_code != 200:
        with anyio.CancelScope(shield=True):
            with anyio.move_on_after(5):
                await upstream.aclose()
        # A 5xx can follow provider work: retaining the reservation is deliberate.
        raise HTTPException(502, "OpenAI did not provide a successful stream")
    return upstream


class RequestCharge:
    """Owns billing and the connection even when the async iterator never starts."""

    def __init__(self, db: RelayDB, reservation: Reservation, price: TokenPrice):
        self.db, self.reservation, self.price = db, reservation, price
        self.upstream: httpx.Response | None = None
        self.usage: tuple[int, int] | None = None
        self.invalid_usage = False
        self.finished = False
        self._cleanup_task: asyncio.Task[None] | None = None
        self.deadline = asyncio.get_running_loop().time() + MAX_REQUEST_SECONDS
        self._timer = asyncio.get_running_loop().call_later(MAX_REQUEST_SECONDS, self._expire)

    def _expire(self) -> None:
        # Covers abandoned responses which were returned but never invoked by ASGI.
        self._cleanup_task = asyncio.create_task(self.close())

    def observe(self, data: str) -> None:
        try:
            obj = json.loads(data)
        except (ValueError, RecursionError):
            self.invalid_usage = True
            return
        if not isinstance(obj, dict) or "error" in obj:
            self.invalid_usage = True
            return
        if obj.get("usage") is None:
            return
        usage = obj["usage"]
        if not isinstance(usage, dict):
            self.invalid_usage = True
            return
        prompt, completion = usage.get("prompt_tokens"), usage.get("completion_tokens")
        if any(type(value) is not int or not 0 <= value <= 2**31 for value in (prompt, completion)):
            self.invalid_usage = True
            return
        assert isinstance(prompt, int) and isinstance(completion, int)
        if prompt == 0 or (
            "total_tokens" in usage
            and (
                type(usage["total_tokens"]) is not int
                or usage["total_tokens"] != prompt + completion
            )
        ):
            self.invalid_usage = True
            return
        # Multiple conflicting usage events are ambiguous, never a reason to refund.
        counts = (prompt, completion)
        if self.usage is not None and self.usage != counts:
            self.invalid_usage = True
        self.usage = counts

    def release(self) -> None:
        if not self.finished:
            self.db.release(self.reservation.id)
            self.finished = True
            self._timer.cancel()

    async def close(self) -> None:
        if self.finished:
            await self._close_upstream()
            return
        # Commit synchronously before cancellable socket cleanup. Failure leaves the
        # original reservation charged and eligible for later reconciliation.
        actual = None
        if self.usage is not None and not self.invalid_usage:
            actual = self.price.cost(*self.usage)
            if actual > self.reservation.reserved_usd:
                log.error(
                    "Usage exceeded reservation %s; recorded the full cost", self.reservation.id
                )
        try:
            self.db.settle(self.reservation.id, actual)
        finally:
            self.finished = True
            self._timer.cancel()
            await self._close_upstream()

    async def _close_upstream(self) -> None:
        if self.upstream is not None:
            with anyio.CancelScope(shield=True):
                with anyio.move_on_after(5):
                    await self.upstream.aclose()

    async def iterate(self) -> AsyncIterator[str]:
        assert self.upstream is not None
        try:
            async for _event, data in iter_sse(self.upstream):
                if self.finished:
                    return
                # Read metadata before yielding: the client may disconnect at this yield.
                if data.strip() != "[DONE]":
                    self.observe(data)
                yield f"data: {data}\n\n"
                if data.strip() == "[DONE]":
                    return
        finally:
            await self.close()


class ChargedResponse(StreamingResponse):
    def __init__(self, charge: RequestCharge):
        self.charge = charge
        super().__init__(charge.iterate(), media_type="text/event-stream")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            remaining = max(0, self.charge.deadline - asyncio.get_running_loop().time())
            with anyio.fail_after(remaining):
                await super().__call__(scope, receive, send)
        finally:
            # Also runs when sending response headers fails before iterate() is entered.
            await self.charge.close()


async def handle_chat_completions(
    db: RelayDB,
    settings: RelaySettings,
    http_client: httpx.AsyncClient,
    friend: Friend,
    body: Any,
) -> ChargedResponse:
    outgoing, input_bound, price = sanitize_body(settings, body)
    if not settings.openai_api_key:
        raise HTTPException(503, "Relay is not configured with an OpenAI key")
    # Round upward at nano-dollar precision, avoiding an under-reservation by roundoff.
    cost = math.ceil(price.cost(input_bound, outgoing["max_completion_tokens"]) * 1e9) / 1e9
    try:
        reservation = db.reserve(
            friend.id,
            outgoing["model"],
            cost,
            settings.max_inflight_per_friend,
            credential_version=friend.credential_version,
        )
    except AdmissionError as e:
        statuses = {"revoked": 401, "budget": 402, "busy": 429, "invalid": 503}
        details = {
            "revoked": "Access has been revoked",
            "budget": f"Insufficient budget for the ${cost:.6f} maximum request reservation",
            "busy": "Too many active requests; wait for an existing request to finish",
            "invalid": "Relay budget state requires owner attention",
        }
        raise HTTPException(statuses[e.reason], details[e.reason]) from None
    except sqlite3.OperationalError:
        raise HTTPException(503, "Relay accounting is temporarily unavailable") from None
    charge = RequestCharge(db, reservation, price)
    try:
        # The durable active charge exists BEFORE any send. Even a crash in send() stays billed.
        with anyio.fail_after(MAX_REQUEST_SECONDS):
            charge.upstream = await open_upstream(http_client, settings, outgoing)
        if charge.finished:
            await charge.close()
            raise HTTPException(502, "OpenAI response arrived after the request deadline")
        return ChargedResponse(charge)
    except (UnsentRequest, RejectedRequest):
        charge.release()
        raise HTTPException(502, "OpenAI could not accept the request") from None
    except (httpx.HTTPError, TimeoutError):
        await charge.close()
        raise HTTPException(502, "OpenAI connection was interrupted; usage is reserved") from None
    except BaseException:
        await charge.close()
        raise
