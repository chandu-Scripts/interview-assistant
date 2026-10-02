import json

import httpx
import pytest
from fastapi.testclient import TestClient
from interview_relay.app import create_app
from interview_relay.config import RelaySettings
from interview_relay.db import RelayDB

ADMIN = ("admin", "s3cret-admin-token")


def sse(*events: object) -> httpx.Response:
    out = ""
    for data in events:
        out += f"data: {data if isinstance(data, str) else json.dumps(data)}\n\n"
    return httpx.Response(200, content=out.encode(), headers={"content-type": "text/event-stream"})


def openai_ok(_req: httpx.Request) -> httpx.Response:
    return sse(
        {"choices": [{"delta": {"role": "assistant"}}]},
        {"choices": [{"delta": {"content": "Hel"}}]},
        {"choices": [{"delta": {"content": "lo"}}]},
        {"choices": [{"delta": {}, "finish_reason": "stop"}]},
        {"choices": [], "usage": {"prompt_tokens": 100, "completion_tokens": 20}},
        "[DONE]",
    )


@pytest.fixture
def settings() -> RelaySettings:
    return RelaySettings(
        openai_api_key="sk-test",
        admin_token=ADMIN[1],
        model_fast="gpt-4o-mini",
        model_smart="gpt-4o",
        max_tokens_ceiling=1600,
        default_budget_usd=2.0,
        default_budget_scope="day",
    )


@pytest.fixture
def db() -> RelayDB:
    d = RelayDB(":memory:")
    yield d
    d.close()


def make_friend(db: RelayDB, budget_usd: float = 2.0, budget_scope: str = "day") -> str:
    """Approves a fresh request and returns the friend's raw token."""
    req = db.create_request("Alice")
    result = db.approve_request(req.id, budget_usd, budget_scope)
    assert result is not None
    _friend, token = result
    return token


def make_client(db: RelayDB, settings: RelaySettings, handler) -> TestClient:
    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app = create_app(db, settings, http_client=http_client)
    return TestClient(app)


# ---------------- auth ----------------
def test_missing_token_is_rejected(db, settings):
    with make_client(db, settings, openai_ok) as c:
        r = c.post(
            "/v1/chat/completions",
            json={"model": "fast", "messages": [{"role": "user", "content": "hi"}]},
        )
    assert r.status_code == 401


def test_garbage_token_is_rejected(db, settings):
    with make_client(db, settings, openai_ok) as c:
        r = c.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer not-a-real-token"},
            json={"model": "fast", "messages": [{"role": "user", "content": "hi"}]},
        )
    assert r.status_code == 401


def test_revoked_friend_is_rejected(db, settings):
    token = make_friend(db)
    (friend,) = db.list_friends()
    db.revoke_friend(friend.id)
    with make_client(db, settings, openai_ok) as c:
        r = c.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {token}"},
            json={"model": "fast", "messages": [{"role": "user", "content": "hi"}]},
        )
    assert r.status_code == 401


# ---------------- request validation ----------------
def test_unknown_tier_is_rejected(db, settings):
    token = make_friend(db)
    with make_client(db, settings, openai_ok) as c:
        r = c.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {token}"},
            json={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
        )
    assert r.status_code == 400
    assert "fast" in r.text and "smart" in r.text


def test_empty_messages_is_rejected(db, settings):
    token = make_friend(db)
    with make_client(db, settings, openai_ok) as c:
        r = c.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {token}"},
            json={"model": "fast", "messages": []},
        )
    assert r.status_code == 400


# ---------------- budget ----------------
def test_budget_exceeded_is_rejected_before_any_upstream_call(db, settings):
    token = make_friend(db, budget_usd=0.01)
    (friend,) = db.list_friends()
    db.add_spend(friend.id, 0.02)  # already over budget

    called = False

    def h(_req):
        nonlocal called
        called = True
        return openai_ok(_req)

    with make_client(db, settings, h) as c:
        r = c.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {token}"},
            json={"model": "fast", "messages": [{"role": "user", "content": "hi"}]},
        )
    assert r.status_code == 402
    assert called is False


def test_fresh_friend_under_budget_is_allowed(db, settings):
    token = make_friend(db, budget_usd=2.0)
    with make_client(db, settings, openai_ok) as c:
        r = c.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {token}"},
            json={"model": "fast", "messages": [{"role": "user", "content": "hi"}]},
        )
    assert r.status_code == 200


# ---------------- model/tier mapping + clamping ----------------
def test_tier_is_mapped_to_the_real_model_server_side(db, settings):
    token = make_friend(db)
    seen = {}

    def h(req):
        seen["body"] = json.loads(req.content)
        return openai_ok(req)

    with make_client(db, settings, h) as c:
        c.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {token}"},
            json={"model": "smart", "messages": [{"role": "user", "content": "hi"}]},
        )
    assert seen["body"]["model"] == "gpt-4o"
    assert seen["body"]["stream"] is True
    assert seen["body"]["stream_options"] == {"include_usage": True}


def test_max_tokens_is_clamped_to_the_ceiling(db, settings):
    token = make_friend(db)
    seen = {}

    def h(req):
        seen["body"] = json.loads(req.content)
        return openai_ok(req)

    with make_client(db, settings, h) as c:
        c.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "model": "fast",
                "messages": [{"role": "user", "content": "hi"}],
                "max_completion_tokens": 999999,
            },
        )
    assert seen["body"]["max_completion_tokens"] == settings.max_tokens_ceiling


def test_requested_max_tokens_under_ceiling_is_respected(db, settings):
    token = make_friend(db)
    seen = {}

    def h(req):
        seen["body"] = json.loads(req.content)
        return openai_ok(req)

    with make_client(db, settings, h) as c:
        c.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "model": "fast",
                "messages": [{"role": "user", "content": "hi"}],
                "max_completion_tokens": 200,
            },
        )
    assert seen["body"]["max_completion_tokens"] == 200


# ---------------- successful stream + billing ----------------
def test_successful_stream_is_relayed_and_billed_from_real_usage(db, settings):
    token = make_friend(db)
    (friend,) = db.list_friends()

    with make_client(db, settings, openai_ok) as c:
        r = c.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {token}"},
            json={"model": "fast", "messages": [{"role": "user", "content": "hi"}]},
        )
    assert r.status_code == 200
    assert "Hel" in r.text and "lo" in r.text and "[DONE]" in r.text

    updated = db.get_friend(friend.id)
    # gpt-4o-mini pricing * (100 prompt, 20 completion) tokens, matching PriceBook.llm_cost exactly
    from interview_assistant.cost.pricing import PriceBook

    expected = PriceBook().llm_cost("openai", "gpt-4o-mini", 100, 20)
    assert updated.spent_usd == pytest.approx(expected)


def test_each_request_is_billed_exactly_once(db, settings):
    token = make_friend(db)
    (friend,) = db.list_friends()

    with make_client(db, settings, openai_ok) as c:
        c.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {token}"},
            json={"model": "fast", "messages": [{"role": "user", "content": "hi"}]},
        )
    spends = db.spend_since(friend.id, 0)
    assert spends > 0
    updated = db.get_friend(friend.id)
    assert updated.spent_usd == pytest.approx(spends)


# ---------------- upstream failure ----------------
def test_upstream_error_status_is_surfaced_without_billing(db, settings):
    token = make_friend(db)
    (friend,) = db.list_friends()

    def h(_req):
        return httpx.Response(401, content=b'{"error": "bad key"}')

    with make_client(db, settings, h) as c:
        r = c.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {token}"},
            json={"model": "fast", "messages": [{"role": "user", "content": "hi"}]},
        )
    assert r.status_code == 502
    assert db.get_friend(friend.id).spent_usd == 0.0


def test_relay_not_configured_with_a_key_is_rejected(db):
    token = make_friend(db)
    unconfigured = RelaySettings(openai_api_key="", admin_token="x")
    with make_client(db, unconfigured, openai_ok) as c:
        r = c.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {token}"},
            json={"model": "fast", "messages": [{"role": "user", "content": "hi"}]},
        )
    assert r.status_code == 503
