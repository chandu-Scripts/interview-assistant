import asyncio
import base64
import json
import sqlite3
import struct
import zlib
from dataclasses import replace

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from interview_relay import app as app_module
from interview_relay import proxy
from interview_relay.app import create_app
from interview_relay.config import RelaySettings
from interview_relay.db import RelayDB
from interview_relay.input_limits import IMAGE_TOKEN_BOUNDS, sanitize_body

BODY = {"model": "fast", "messages": [{"role": "user", "content": "hi"}]}


@pytest.fixture
def funded():
    db = RelayDB(":memory:")
    friend, token = db.approve_request(db.create_request("Synthetic").id, 2, "day")
    settings = RelaySettings(
        openai_api_key="synthetic-key",
        admin_token="synthetic-admin",
        model_fast="gpt-4o-mini",
        model_smart="gpt-4o",
        max_tokens_ceiling=1600,
    )
    yield db, friend, token, settings
    db.close()


def sse(*events):
    payload = "".join(f"data: {e if isinstance(e, str) else json.dumps(e)}\n\n" for e in events)
    return httpx.Response(200, content=payload.encode())


def reservation_cost(settings, body=BODY):
    outgoing, bound, price = sanitize_body(settings, body)
    import math

    return math.ceil(price.cost(bound, outgoing["max_completion_tokens"]) * 1e9) / 1e9


def test_simultaneous_proxy_calls_cannot_both_use_same_remaining_budget(funded):
    db, friend, _, settings = funded
    cost = reservation_cost(settings)
    db.set_budget(friend.id, cost * 1.5, "day")
    calls = []

    async def handler(request):
        calls.append(request)
        await asyncio.sleep(0)
        return sse("[DONE]")

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            results = await asyncio.gather(
                *[
                    proxy.handle_chat_completions(db, settings, client, friend, BODY)
                    for _ in range(2)
                ],
                return_exceptions=True,
            )
            assert len(calls) == 1
            errors = [item for item in results if isinstance(item, HTTPException)]
            assert len(errors) == 1 and errors[0].status_code == 402
            for item in results:
                if isinstance(item, proxy.ChargedResponse):
                    await item.charge.close()
        assert db.spend_since(friend.id, 0) == pytest.approx(cost)

    asyncio.run(run())


@pytest.mark.parametrize(
    "failure, retained",
    [(401, False), (429, False), (500, True), ("connect", False), ("read", True)],
)
def test_only_proven_unsent_or_rejected_requests_release(funded, failure, retained):
    db, friend, token, settings = funded

    def handler(request):
        if failure == "connect":
            raise httpx.ConnectError("synthetic", request=request)
        if failure == "read":
            raise httpx.ReadTimeout("synthetic", request=request)
        return httpx.Response(failure, content=b"sensitive upstream reason")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with TestClient(create_app(db, settings, http_client=client)) as test:
        result = test.post(
            "/v1/chat/completions", json=BODY, headers={"Authorization": f"Bearer {token}"}
        )
    assert result.status_code == 502
    assert "sensitive" not in result.text
    expected = reservation_cost(settings) if retained else 0
    assert db.spend_since(friend.id, 0) == pytest.approx(expected)


def test_cancel_awaiting_headers_retains_charge(funded):
    db, friend, _, settings = funded

    async def run():
        entered = asyncio.Event()

        async def handler(request):
            entered.set()
            await asyncio.Event().wait()

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            task = asyncio.create_task(
                proxy.handle_chat_completions(db, settings, client, friend, BODY)
            )
            await entered.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert db.spend_since(friend.id, 0) == pytest.approx(reservation_cost(settings))
        assert db._db.execute("SELECT status FROM reservations").fetchone()[0] == "estimated"

    asyncio.run(run())


def test_disconnect_before_iterator_starts_still_closes_and_accounts(funded):
    db, friend, _, settings = funded

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: sse("[DONE]"))
        ) as client:
            response = await proxy.handle_chat_completions(db, settings, client, friend, BODY)

            async def send(_message):
                raise RuntimeError("synthetic disconnect before first byte")

            async def receive():
                return {"type": "http.disconnect"}

            with pytest.raises(RuntimeError, match="synthetic disconnect"):
                await response({"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send)
            assert response.charge.upstream.is_closed
            assert db.get_reservation(response.charge.reservation.id).status == "estimated"
        assert db.spend_since(friend.id, 0) == pytest.approx(reservation_cost(settings))

    asyncio.run(run())


def test_unconsumed_response_expires_without_refund(funded, monkeypatch):
    db, friend, _, settings = funded
    monkeypatch.setattr(proxy, "MAX_REQUEST_SECONDS", 0.02)

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: sse("[DONE]"))
        ) as client:
            response = await proxy.handle_chat_completions(db, settings, client, friend, BODY)
            await asyncio.sleep(0.06)
            assert response.charge.finished
            assert response.charge.upstream.is_closed
            assert db.get_reservation(response.charge.reservation.id).status == "estimated"
        assert db.spend_since(friend.id, 0) == pytest.approx(reservation_cost(settings))

    asyncio.run(run())


@pytest.mark.parametrize(
    "usage",
    [
        [],
        "bad",
        {},
        {"prompt_tokens": -1, "completion_tokens": 2},
        {"prompt_tokens": True, "completion_tokens": 2},
        {"prompt_tokens": 0, "completion_tokens": 0},
        {"prompt_tokens": 10, "completion_tokens": float("nan")},
        {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 1},
    ],
)
def test_malformed_usage_keeps_reserved_cost(funded, usage):
    db, friend, token, settings = funded
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: sse({"choices": "malformed", "usage": usage}, "[DONE]")
        )
    )
    with TestClient(create_app(db, settings, http_client=client)) as test:
        result = test.post(
            "/v1/chat/completions", json=BODY, headers={"Authorization": f"Bearer {token}"}
        )
    assert result.status_code == 200
    assert db.spend_since(friend.id, 0) == pytest.approx(reservation_cost(settings))


def test_usage_is_observed_before_client_disconnects_at_yield(funded):
    db, friend, _, settings = funded

    async def run():
        def handler(_request):
            return sse({"usage": {"prompt_tokens": 100, "completion_tokens": 20}})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            response = await proxy.handle_chat_completions(db, settings, client, friend, BODY)
            await anext(response.body_iterator)
            await response.body_iterator.aclose()
            await response.charge.close()
        _, _, price = sanitize_body(settings, BODY)
        assert db.spend_since(friend.id, 0) == pytest.approx(price.cost(100, 20))

    asyncio.run(run())


@pytest.mark.parametrize(
    "body",
    [
        [],
        None,
        "string",
        {**BODY, "model": []},
        {**BODY, "messages": [1]},
        {**BODY, "messages": [{"role": "user", "content": None}]},
        {**BODY, "messages": [{"role": "user", "content": [{"type": "file", "file": "x"}]}]},
        {**BODY, "n": 2},
        {**BODY, "tools": []},
        {**BODY, "service_tier": "priority"},
        {**BODY, "max_completion_tokens": True},
        {**BODY, "temperature": float("nan")},
    ],
)
def test_malformed_and_unpriced_features_never_contact_upstream(funded, body):
    db, friend, token, settings = funded
    calls = []

    def handler(request):
        calls.append(request)
        return sse("[DONE]")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with TestClient(create_app(db, settings, http_client=client)) as test:
        result = test.post(
            "/v1/chat/completions",
            content=json.dumps(body),
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
    assert result.status_code == 400
    assert not calls
    assert db.spend_since(friend.id, 0) == 0


def test_body_limit_is_enforced_before_json_parsing(funded, monkeypatch):
    db, friend, token, settings = funded
    monkeypatch.setattr(app_module, "MAX_BODY_BYTES", 32)
    calls = []
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: calls.append(request)))
    with TestClient(create_app(db, settings, http_client=client)) as test:
        result = test.post(
            "/v1/chat/completions", content=b"{" * 33, headers={"Authorization": f"Bearer {token}"}
        )
    assert result.status_code == 413
    assert not calls
    assert db.spend_since(friend.id, 0) == 0


def test_unknown_model_price_fails_before_provider(funded):
    db, friend, token, settings = funded
    settings = replace(settings, model_fast="gpt-4o-unknown-unpriced-variant")
    calls = []
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: calls.append(request)))
    with TestClient(create_app(db, settings, http_client=client)) as test:
        result = test.post(
            "/v1/chat/completions", json=BODY, headers={"Authorization": f"Bearer {token}"}
        )
    assert result.status_code == 503
    assert not calls
    assert db.spend_since(friend.id, 0) == 0


def png_url(width=1, height=1):
    def chunk(kind, data):
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    raw = b"\x89PNG\r\n\x1a\n"
    raw += chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    raw += chunk(b"IDAT", zlib.compress(b"\0\0\0\0"))
    raw += chunk(b"IEND", b"")
    return "data:image/png;base64," + base64.b64encode(raw).decode()


@pytest.mark.parametrize("model", list(IMAGE_TOKEN_BOUNDS))
def test_inline_png_preserves_bytes_and_detail_with_full_image_reservation(funded, model):
    _, _, _, settings = funded
    settings = replace(settings, model_fast=model)
    image = {"url": png_url(), "detail": "high"}
    body = {
        "model": "fast",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Read this"},
                    {"type": "image_url", "image_url": image},
                ],
            }
        ],
    }
    outgoing, bound, _ = sanitize_body(settings, body)
    assert outgoing["messages"] == body["messages"]
    assert bound >= IMAGE_TOKEN_BOUNDS[model] + len(b"Read this")


@pytest.mark.parametrize(
    "url",
    ["https://example.invalid/image.png", "data:image/png;base64,garbage", png_url(width=9000)],
)
def test_invalid_or_remote_images_are_rejected(funded, url):
    _, _, _, settings = funded
    body = {
        "model": "fast",
        "messages": [
            {"role": "user", "content": [{"type": "image_url", "image_url": {"url": url}}]}
        ],
    }
    with pytest.raises(HTTPException) as error:
        sanitize_body(settings, body)
    assert error.value.status_code == 400


def test_unicode_text_uses_utf8_byte_bound_not_character_estimate(funded):
    _, _, _, settings = funded
    text = "\u6f22\u5b57\U0001f642" * 100
    _, bound, _ = sanitize_body(
        settings, {"model": "fast", "messages": [{"role": "user", "content": text}]}
    )
    assert bound == 256 + len(text.encode("utf-8"))


def test_stale_authenticated_friend_is_rechecked_before_provider(funded):
    db, friend, _, settings = funded
    db.revoke_friend(friend.id)

    async def run():
        def handler(_request):
            pytest.fail("A revoked snapshot reached the provider")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(HTTPException) as error:
                await proxy.handle_chat_completions(db, settings, client, friend, BODY)
            assert error.value.status_code == 401
        assert db.spend_since(friend.id, 0) == 0

    asyncio.run(run())


def test_rotated_token_invalidates_snapshot_before_admission(funded):
    db, friend, old_token, settings = funded
    new_token = db.reissue_token(friend.id)
    assert db.get_friend_by_token(old_token) is None
    fresh = db.get_friend_by_token(new_token)
    assert fresh.credential_version == friend.credential_version + 1
    calls = []

    def handler(request):
        calls.append(request)
        return sse("[DONE]")

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(HTTPException) as error:
                await proxy.handle_chat_completions(db, settings, client, friend, BODY)
            assert error.value.status_code == 401
            assert not calls
            response = await proxy.handle_chat_completions(db, settings, client, fresh, BODY)
            await response.charge.close()
            assert len(calls) == 1

    asyncio.run(run())


def test_settlement_write_failure_keeps_reservation_and_closes_connection(funded, monkeypatch):
    db, friend, _, settings = funded

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: sse("[DONE]"))
        ) as client:
            response = await proxy.handle_chat_completions(db, settings, client, friend, BODY)

            def broken(*_args):
                raise sqlite3.OperationalError("synthetic unavailable database")

            monkeypatch.setattr(db, "settle", broken)
            with pytest.raises(sqlite3.OperationalError):
                async for _ in response.body_iterator:
                    pass
            assert response.charge.upstream.is_closed
            assert db.spend_since(friend.id, 0) == pytest.approx(reservation_cost(settings))
            assert db.get_reservation(response.charge.reservation.id).status == "active"

    asyncio.run(run())


def test_inline_screenshot_reaches_provider_unchanged_and_prepaid(funded):
    db, friend, token, settings = funded
    body = {
        "model": "fast",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Explain this screenshot"},
                    {"type": "image_url", "image_url": {"url": png_url(), "detail": "high"}},
                ],
            }
        ],
    }
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        assert db.spend_since(friend.id, 0) == pytest.approx(reservation_cost(settings, body))
        return sse({"usage": {"prompt_tokens": 1000, "completion_tokens": 20}}, "[DONE]")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with TestClient(create_app(db, settings, http_client=client)) as test:
        result = test.post(
            "/v1/chat/completions", json=body, headers={"Authorization": f"Bearer {token}"}
        )
    assert result.status_code == 200
    assert seen[0]["messages"] == body["messages"]


def test_estimated_charge_is_visible_to_owner(funded):
    db, friend, _, settings = funded
    reservation = db.reserve(friend.id, "gpt-4o-mini", 0.1, 2)
    db.settle(reservation.id, None)
    with TestClient(create_app(db, settings)) as test:
        result = test.get("/admin", auth=("admin", settings.admin_token))
    assert result.status_code == 200
    assert reservation.id in result.text and "estimated" in result.text
    assert "Spent + reserved" in result.text


@pytest.mark.parametrize("amount", ["nan", "inf", "-1"])
def test_admin_cannot_create_invalid_budget(funded, amount):
    db, _, _, settings = funded
    pending = db.create_request("New synthetic friend")
    with TestClient(create_app(db, settings)) as test:
        result = test.post(
            f"/admin/approve/{pending.id}",
            data={"budget_usd": amount},
            auth=("admin", settings.admin_token),
        )
    assert result.status_code == 422
    assert db.get_request(pending.public_id).status == "pending"
