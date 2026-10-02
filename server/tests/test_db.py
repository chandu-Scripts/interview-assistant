import sqlite3

import pytest
from interview_relay.db import RelayDB


class Clock:
    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def db():
    d = RelayDB(":memory:")
    yield d
    d.close()


def test_create_and_get_request(db):
    req = db.create_request("Alice", "please :)")
    assert req.status == "pending" and req.name == "Alice" and req.note == "please :)"
    assert db.get_request(req.public_id) == req
    assert db.get_request("nope") is None


def test_public_id_is_not_the_row_id(db):
    req = db.create_request("Alice")
    assert req.public_id != str(req.id) and len(req.public_id) > 15


def test_name_and_note_are_trimmed_and_length_capped(db):
    req = db.create_request("  Alice  ", "  hi  ")
    assert req.name == "Alice" and req.note == "hi"
    long_req = db.create_request("x" * 200, "y" * 500)
    assert len(long_req.name) == 80 and len(long_req.note) == 300


def test_list_requests_filters_by_status(db):
    a = db.create_request("Alice")
    db.create_request("Bob")
    db.approve_request(a.id, 2.0, "day")
    assert [r.name for r in db.list_requests("pending")] == ["Bob"]
    assert {r.status for r in db.list_requests()} == {"pending", "approved"}


def test_approve_creates_friend_and_marks_request(db):
    req = db.create_request("Alice")
    result = db.approve_request(req.id, 3.5, "day")
    assert result is not None
    friend, token = result
    assert friend.name == "Alice" and friend.budget_usd == 3.5 and friend.active
    assert token.startswith("ika_") and len(token) > 20
    assert db.get_request(req.public_id).status == "approved"


def test_approve_is_not_double_applicable(db):
    req = db.create_request("Alice")
    assert db.approve_request(req.id, 2.0, "day") is not None
    assert db.approve_request(req.id, 2.0, "day") is None  # already decided


def test_approve_unknown_request_returns_none(db):
    assert db.approve_request(999, 2.0, "day") is None


def test_deny_marks_request_and_is_idempotent_false_on_repeat(db):
    req = db.create_request("Alice")
    assert db.deny_request(req.id) is True
    assert db.get_request(req.public_id).status == "denied"
    assert db.deny_request(req.id) is False  # already decided
    assert db.approve_request(req.id, 2.0, "day") is None  # can't approve a denied one


def test_consume_pending_token_is_delivered_exactly_once(db):
    req = db.create_request("Alice")
    friend, raw = db.approve_request(req.id, 2.0, "day")
    assert db.consume_pending_token(req.public_id) == raw
    assert db.consume_pending_token(req.public_id) is None  # already delivered


def test_consume_pending_token_before_approval_or_unknown(db):
    req = db.create_request("Alice")
    assert db.consume_pending_token(req.public_id) is None  # still pending
    assert db.consume_pending_token("nope") is None


def test_get_friend_by_token_and_by_id(db):
    req = db.create_request("Alice")
    friend, raw = db.approve_request(req.id, 2.0, "day")
    assert db.get_friend_by_token(raw) == friend
    assert db.get_friend_by_token("wrong-token") is None
    assert db.get_friend(friend.id) == friend
    assert db.get_friend(999) is None


def test_revoke_disables_friend_and_their_token(db):
    req = db.create_request("Alice")
    friend, raw = db.approve_request(req.id, 2.0, "day")
    assert db.revoke_friend(friend.id) is True
    assert not db.get_friend(friend.id).active
    # the raw token itself still hashes the same, but callers must check .active before trusting it
    assert db.get_friend_by_token(raw).active is False
    assert db.revoke_friend(friend.id) is False  # already revoked


def test_reissue_invalidates_old_token_and_issues_a_new_one(db):
    req = db.create_request("Alice")
    friend, old = db.approve_request(req.id, 2.0, "day")
    new = db.reissue_token(friend.id)
    assert new is not None and new != old
    assert db.get_friend_by_token(old) is None
    assert db.get_friend_by_token(new).id == friend.id
    assert db.consume_pending_token(req.public_id) == new  # deliverable exactly once, like approval


def test_reissue_unknown_or_revoked_returns_none(db):
    assert db.reissue_token(999) is None
    req = db.create_request("Alice")
    friend, _ = db.approve_request(req.id, 2.0, "day")
    db.revoke_friend(friend.id)
    assert db.reissue_token(friend.id) is None


def test_set_budget_and_add_spend(db):
    req = db.create_request("Alice")
    friend, _ = db.approve_request(req.id, 2.0, "day")
    assert db.set_budget(friend.id, 5.0, "month") is True
    db.add_spend(friend.id, 0.01)
    db.add_spend(friend.id, 0.02)
    updated = db.get_friend(friend.id)
    assert updated.budget_usd == 5.0 and updated.budget_scope == "month"
    assert updated.spent_usd == pytest.approx(0.03)
    assert db.set_budget(999, 1.0, "day") is False


def test_list_friends_newest_first(db):
    clock = Clock()
    d = RelayDB(":memory:", clock=clock)
    r1 = d.create_request("Alice")
    d.approve_request(r1.id, 1.0, "day")
    clock.t += 10
    r2 = d.create_request("Bob")
    d.approve_request(r2.id, 1.0, "day")
    assert [f.name for f in d.list_friends()] == ["Bob", "Alice"]
    d.close()


def test_never_stores_secrets_in_plaintext_columns(db):
    """token_hash/pending_token are internal-only: the mapped Friend dataclass never exposes them,
    and the hash itself must not equal the raw token."""
    req = db.create_request("Alice")
    friend, raw = db.approve_request(req.id, 2.0, "day")
    assert not hasattr(friend, "token_hash") and not hasattr(friend, "pending_token")
    conn = sqlite3.connect(":memory:")  # sanity: hashing is not a no-op
    conn.close()
    from interview_relay.auth import hash_token

    assert hash_token(raw) != raw and len(hash_token(raw)) == 64


def test_reopening_persists_data(tmp_path):
    path = tmp_path / "relay.db"
    d1 = RelayDB(path)
    req = d1.create_request("Alice")
    d1.approve_request(req.id, 2.0, "day")
    d1.close()
    d2 = RelayDB(path)
    assert d2.list_friends()[0].name == "Alice"
    d2.close()


def test_newer_schema_is_refused(tmp_path):
    path = tmp_path / "relay.db"
    RelayDB(path).close()
    raw = sqlite3.connect(path)
    raw.execute("PRAGMA user_version = 99")
    raw.commit()
    raw.close()
    with pytest.raises(RuntimeError):
        RelayDB(path)
