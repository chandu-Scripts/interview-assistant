import pytest
from fastapi.testclient import TestClient
from interview_relay.app import create_app
from interview_relay.config import RelaySettings
from interview_relay.db import RelayDB

ADMIN = ("admin", "s3cret-admin-token")


@pytest.fixture
def client():
    settings = RelaySettings(
        openai_api_key="sk-test",
        admin_token=ADMIN[1],
        default_budget_usd=2.0,
        default_budget_scope="day",
    )
    db = RelayDB(":memory:")
    app = create_app(db, settings)
    with TestClient(app) as c:
        c.relay_db = db  # stash for direct inspection in a couple of tests
        yield c
    db.close()


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json() == {"ok": True, "missing_config": []}


def test_health_flags_missing_config():
    app = create_app(RelayDB(":memory:"), RelaySettings(openai_api_key="", admin_token=""))
    r = TestClient(app).get("/health")
    assert r.json()["ok"] is False
    assert set(r.json()["missing_config"]) == {"ADMIN_TOKEN", "OPENAI_API_KEY"}


# ---------------- friend-facing ----------------
def test_submit_request_and_check_pending(client):
    r = client.post("/requests", json={"name": "Alice", "note": "hi"})
    assert r.status_code == 200
    public_id = r.json()["public_id"]
    status = client.get(f"/requests/{public_id}").json()
    assert status == {"status": "pending", "token": None, "budget_usd": None, "budget_scope": None}


def test_unknown_request_id_is_404(client):
    assert client.get("/requests/does-not-exist").status_code == 404


def test_request_validation_rejects_empty_name(client):
    r = client.post("/requests", json={"name": ""})
    assert r.status_code == 422


def test_request_name_too_long_is_rejected(client):
    r = client.post("/requests", json={"name": "x" * 81})
    assert r.status_code == 422


def test_full_approval_lifecycle_delivers_token_exactly_once(client):
    public_id = client.post("/requests", json={"name": "Alice"}).json()["public_id"]
    (req_id,) = [r.id for r in client.relay_db.list_requests("pending")]

    r = client.post(f"/admin/approve/{req_id}", data={"budget_usd": "3"}, auth=ADMIN)
    assert r.status_code == 200 and "Approved" in r.text  # followed the redirect

    first = client.get(f"/requests/{public_id}").json()
    assert first["status"] == "approved" and first["token"].startswith("ika_")
    assert first["budget_usd"] == 3.0 and first["budget_scope"] == "day"

    second = client.get(f"/requests/{public_id}").json()
    assert second == {"status": "approved", "token": None, "budget_usd": 3.0, "budget_scope": "day"}


def test_deny_lifecycle(client):
    public_id = client.post("/requests", json={"name": "Bob"}).json()["public_id"]
    (req_id,) = [r.id for r in client.relay_db.list_requests("pending")]
    client.post(f"/admin/deny/{req_id}", auth=ADMIN)
    assert client.get(f"/requests/{public_id}").json()["status"] == "denied"


# ---------------- admin auth ----------------
def test_admin_routes_require_correct_credentials(client):
    assert client.get("/admin").status_code == 401  # no credentials
    assert client.get("/admin", auth=("admin", "wrong")).status_code == 401
    assert client.get("/admin", auth=("someone-else", ADMIN[1])).status_code == 401
    assert client.get("/admin", auth=ADMIN).status_code == 200


def test_blank_admin_token_never_authenticates():
    app = create_app(RelayDB(":memory:"), RelaySettings(admin_token=""))
    r = TestClient(app).get("/admin", auth=("admin", ""))
    assert r.status_code == 401


def test_admin_dashboard_lists_pending_and_friends(client):
    client.post("/requests", json={"name": "Alice", "note": "please"})
    html = client.get("/admin", auth=ADMIN).text
    assert "Alice" in html and "please" in html and "No approved friends yet" in html


def test_admin_actions_without_auth_are_rejected(client):
    public_id = client.post("/requests", json={"name": "Alice"}).json()["public_id"]
    (req_id,) = [r.id for r in client.relay_db.list_requests("pending")]
    assert client.post(f"/admin/approve/{req_id}", data={"budget_usd": "2"}).status_code == 401
    assert client.get(f"/requests/{public_id}").json()["status"] == "pending"  # untouched


def test_revoke_and_reissue_flow(client):
    public_id = client.post("/requests", json={"name": "Alice"}).json()["public_id"]
    (req_id,) = [r.id for r in client.relay_db.list_requests("pending")]
    client.post(f"/admin/approve/{req_id}", data={"budget_usd": "2"}, auth=ADMIN)
    old_token = client.get(f"/requests/{public_id}").json()["token"]
    (friend_id,) = [f.id for f in client.relay_db.list_friends()]

    r = client.post(f"/admin/reissue/{friend_id}", auth=ADMIN)
    assert r.status_code == 200 and "New token for Alice" in r.text
    new_token = r.text.split("<code>")[1].split("</code>")[0]
    assert new_token != old_token
    assert client.relay_db.get_friend_by_token(old_token) is None
    assert client.relay_db.get_friend_by_token(new_token).active

    r = client.post(f"/admin/revoke/{friend_id}", auth=ADMIN)
    assert r.status_code == 200 and "revoked" in r.text.lower()
    assert not client.relay_db.get_friend(friend_id).active


def test_reissue_unknown_friend_redirects_with_message(client):
    r = client.post("/admin/reissue/999", auth=ADMIN)
    assert r.status_code == 200 and "Could not reissue" in r.text


def test_approving_twice_shows_a_clear_message_not_a_crash(client):
    client.post("/requests", json={"name": "Alice"})
    (req_id,) = [r.id for r in client.relay_db.list_requests("pending")]
    client.post(f"/admin/approve/{req_id}", data={"budget_usd": "2"}, auth=ADMIN)
    r = client.post(f"/admin/approve/{req_id}", data={"budget_usd": "2"}, auth=ADMIN)
    assert r.status_code == 200 and "could not be approved" in r.text.lower()


def test_full_two_friend_flow_is_isolated_per_friend(client):
    """Regression guard: two friends' requests/tokens/budgets must never cross."""
    a_id = client.post("/requests", json={"name": "Alice"}).json()["public_id"]
    b_id = client.post("/requests", json={"name": "Bob"}).json()["public_id"]
    reqs = {r.name: r.id for r in client.relay_db.list_requests("pending")}
    client.post(f"/admin/approve/{reqs['Alice']}", data={"budget_usd": "1"}, auth=ADMIN)
    client.post(f"/admin/approve/{reqs['Bob']}", data={"budget_usd": "9"}, auth=ADMIN)
    a = client.get(f"/requests/{a_id}").json()
    b = client.get(f"/requests/{b_id}").json()
    assert a["budget_usd"] == 1.0 and b["budget_usd"] == 9.0
    assert a["token"] != b["token"]
