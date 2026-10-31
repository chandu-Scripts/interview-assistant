import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import pytest
from interview_relay.db import _SCHEMA, AdmissionError, RelayDB, start_of_scope


def approved(db, budget=2.0, scope="day"):
    request = db.create_request("Synthetic friend")
    friend, _ = db.approve_request(request.id, budget, scope)
    return friend


def test_separate_connections_admit_only_one_affordable_request(tmp_path):
    first = RelayDB(tmp_path / "relay.db")
    second = RelayDB(tmp_path / "relay.db")
    friend = approved(first, 1.0)
    barrier = threading.Barrier(2)

    def reserve(db):
        barrier.wait()
        try:
            return db.reserve(friend.id, "gpt-4o-mini", 0.6, 8)
        except AdmissionError as error:
            assert error.reason == "budget"
            return None

    try:
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(reserve, (first, second)))
        assert sum(result is not None for result in results) == 1
        assert first.spend_since(friend.id, 0) == pytest.approx(0.6)
        assert first.get_friend(friend.id).spent_usd == pytest.approx(0.6)
    finally:
        first.close()
        second.close()


def test_separate_connections_cannot_duplicate_approval_or_token_delivery(tmp_path):
    first = RelayDB(tmp_path / "relay.db")
    second = RelayDB(tmp_path / "relay.db")
    pending = first.create_request("Synthetic")
    barrier = threading.Barrier(2)

    def approve(db):
        barrier.wait()
        return db.approve_request(pending.id, 1.0, "day")

    def consume(db):
        barrier.wait()
        return db.consume_pending_token(pending.public_id)

    try:
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(approve, (first, second)))
            assert sum(result is not None for result in results) == 1
            tokens = list(pool.map(consume, (first, second)))
        assert sum(token is not None for token in tokens) == 1
        assert len(first.list_friends()) == 1
    finally:
        first.close()
        second.close()


def test_fresh_budget_and_revocation_are_read_inside_admission(tmp_path):
    first, second = RelayDB(tmp_path / "relay.db"), RelayDB(tmp_path / "relay.db")
    friend = approved(first)
    try:
        second.set_budget(friend.id, 0.1, "day")
        with pytest.raises(AdmissionError, match="budget"):
            first.reserve(friend.id, "gpt-4o-mini", 0.2, 2)
        second.revoke_friend(friend.id)
        with pytest.raises(AdmissionError, match="revoked"):
            first.reserve(friend.id, "gpt-4o-mini", 0.01, 2)
        assert first.spend_since(friend.id, 0) == 0
    finally:
        first.close()
        second.close()


def test_concurrency_limit_is_separate_from_budget_and_other_friends():
    db = RelayDB(":memory:")
    first, other = approved(db), approved(db)
    try:
        pending = db.reserve(first.id, "gpt-4o-mini", 0.1, 1)
        with pytest.raises(AdmissionError, match="busy"):
            db.reserve(first.id, "gpt-4o-mini", 0.1, 1)
        db.reserve(other.id, "gpt-4o-mini", 0.1, 1)
        db.settle(pending.id, None)
        db.reserve(first.id, "gpt-4o-mini", 0.1, 1)
        assert db.spend_since(first.id, 0) == pytest.approx(0.2)
    finally:
        db.close()


def test_settlement_is_idempotent_across_connections(tmp_path):
    first, second = RelayDB(tmp_path / "relay.db"), RelayDB(tmp_path / "relay.db")
    friend = approved(first)
    reservation = first.reserve(friend.id, "gpt-4o-mini", 0.6, 2)
    barrier = threading.Barrier(2)

    def settle(args):
        db, amount = args
        barrier.wait()
        return db.settle(reservation.id, amount)

    try:
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(settle, ((first, 0.1), (second, 0.2))))
        assert sum(results) == 1
        assert first.spend_since(friend.id, 0) in (0.1, 0.2)
        assert not first.release(reservation.id)
        assert first.get_friend(friend.id).spent_usd == pytest.approx(
            first.spend_since(friend.id, 0)
        )
    finally:
        first.close()
        second.close()


@pytest.mark.parametrize("scope", ["day", "month"])
def test_settlement_belongs_to_request_start_period(scope):
    now = [datetime(2026, 1, 31, 23, 59, 59, tzinfo=UTC).timestamp()]
    db = RelayDB(":memory:", clock=lambda: now[0])
    friend = approved(db, 1.0, scope)
    try:
        pending = db.reserve(friend.id, "gpt-4o-mini", 0.6, 2)
        now[0] += 2
        db.settle(pending.id, 0.4)
        assert db.spend_since(friend.id, start_of_scope(scope, now[0])) == 0
        db.reserve(friend.id, "gpt-4o-mini", 1.0, 2)
        assert db.get_friend(friend.id).spent_usd == pytest.approx(1.4)
    finally:
        db.close()


def test_crash_reopen_and_expired_lease_keep_cost_but_free_concurrency(tmp_path):
    now = [1_800_000_000.0]
    path = tmp_path / "relay.db"
    db = RelayDB(path, clock=lambda: now[0])
    friend = approved(db)
    pending = db.reserve(friend.id, "gpt-4o-mini", 0.6, 1)
    db.close()
    now[0] += 181
    db = RelayDB(path, clock=lambda: now[0])
    try:
        assert db.spend_since(friend.id, 0) == pytest.approx(0.6)
        db.reserve(friend.id, "gpt-4o-mini", 0.6, 1)
        assert db.get_reservation(pending.id).status == "estimated"
        assert db.spend_since(friend.id, 0) == pytest.approx(1.2)
        assert db.settle(pending.id, 0.1)  # Later trusted provider reconciliation.
        assert db.spend_since(friend.id, 0) == pytest.approx(0.7)
    finally:
        db.close()


def test_actual_overage_is_recorded_and_blocks_later_requests():
    db = RelayDB(":memory:")
    friend = approved(db, 1.0)
    try:
        pending = db.reserve(friend.id, "gpt-4o-mini", 0.5, 2)
        assert db.settle(pending.id, 1.2)
        assert db.spend_since(friend.id, 0) == pytest.approx(1.2)
        with pytest.raises(AdmissionError, match="budget"):
            db.reserve(friend.id, "gpt-4o-mini", 0.1, 2)
    finally:
        db.close()


def test_lowering_budget_keeps_existing_reservation_and_blocks_new_admission():
    db = RelayDB(":memory:")
    friend = approved(db, 2.0)
    try:
        pending = db.reserve(friend.id, "gpt-4o-mini", 0.8, 2)
        db.set_budget(friend.id, 0.5, "day")
        assert db.spend_since(friend.id, 0) == 0.8
        with pytest.raises(AdmissionError, match="budget"):
            db.reserve(friend.id, "gpt-4o-mini", 0.1, 2)
        db.settle(pending.id, 0.2)
        db.reserve(friend.id, "gpt-4o-mini", 0.2, 2)
        assert db.spend_since(friend.id, 0) == pytest.approx(0.4)
    finally:
        db.close()


@pytest.mark.parametrize("amount", [-1, float("inf"), float("nan"), True])
def test_invalid_financial_values_never_mutate_budget_or_spend(amount):
    db = RelayDB(":memory:")
    friend = approved(db)
    try:
        with pytest.raises(ValueError):
            db.set_budget(friend.id, amount, "day")
        with pytest.raises(ValueError):
            db.add_spend(friend.id, amount)
        with pytest.raises(ValueError):
            db.reserve(friend.id, "gpt-4o-mini", amount, 2)
        assert db.get_friend(friend.id).budget_usd == 2
        assert db.spend_since(friend.id, 0) == 0
    finally:
        db.close()


def test_migration_preserves_v1_records_and_spend(tmp_path):
    path = tmp_path / "legacy.db"
    raw = sqlite3.connect(path)
    raw.executescript(_SCHEMA)
    raw.execute("INSERT INTO requests VALUES (1, 'public', 'Alice', '', 'approved', 1, 1)")
    raw.execute("INSERT INTO friends VALUES (1, 1, 'Alice', 'hash', NULL, 2, 'day', 0.25, 1, NULL)")
    raw.execute("INSERT INTO spends VALUES (1, 1, 1, 0.25)")
    raw.execute("PRAGMA user_version = 1")
    raw.commit()
    raw.close()
    db = RelayDB(path)
    try:
        assert db.get_friend(1).name == "Alice"
        assert db.get_friend(1).spent_usd == 0.25
        assert db.spend_since(1, 0) == 0.25
        pending = db.reserve(1, "gpt-4o-mini", 0.1, 2)
        assert db.get_reservation(pending.id).status == "active"
        assert db._db.execute("PRAGMA user_version").fetchone()[0] == 2
    finally:
        db.close()
