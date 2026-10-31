"""SQLite storage for access requests and approved friends.

Metadata only, matching the desktop app's own privacy rule (interview_assistant.cost.tracker's
UsageStore): this database never sees a prompt or an answer, only who was approved, their budget,
and how much of it they've spent. Thread-safe the same way interview_assistant.history.store is:
one connection, a lock, check_same_thread=False (FastAPI runs sync handlers in a thread pool)."""

import math
import sqlite3
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from interview_relay.auth import generate_token, hash_token, new_public_id

SCHEMA_VERSION = 2

_SCHEMA = """
CREATE TABLE requests (
    id INTEGER PRIMARY KEY,
    public_id TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    created_at REAL NOT NULL,
    decided_at REAL
);
CREATE INDEX requests_public_id ON requests(public_id);
CREATE TABLE friends (
    id INTEGER PRIMARY KEY,
    request_id INTEGER NOT NULL REFERENCES requests(id),
    name TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    pending_token TEXT,
    budget_usd REAL NOT NULL,
    budget_scope TEXT NOT NULL DEFAULT 'day',
    spent_usd REAL NOT NULL DEFAULT 0.0,
    created_at REAL NOT NULL,
    revoked_at REAL
);
CREATE INDEX friends_token_hash ON friends(token_hash);
CREATE INDEX friends_request_id ON friends(request_id);
CREATE TABLE spends (
    id INTEGER PRIMARY KEY,
    friend_id INTEGER NOT NULL REFERENCES friends(id),
    ts REAL NOT NULL,
    usd REAL NOT NULL
);
CREATE INDEX spends_friend_ts ON spends(friend_id, ts);
"""

_RESERVATIONS_SCHEMA = """
CREATE TABLE reservations (
    id TEXT PRIMARY KEY,
    friend_id INTEGER NOT NULL REFERENCES friends(id),
    spend_id INTEGER NOT NULL UNIQUE REFERENCES spends(id),
    started_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    model TEXT NOT NULL,
    reserved_usd REAL NOT NULL CHECK(reserved_usd >= 0),
    status TEXT NOT NULL CHECK(status IN ('active', 'estimated', 'settled', 'released'))
);
CREATE INDEX reservations_friend_status ON reservations(friend_id, status);
"""


def validate_money(value: float) -> None:
    if isinstance(value, bool) or not math.isfinite(value) or value < 0:
        raise ValueError("Dollar amounts must be finite and non-negative")


def start_of_scope(scope: str, now: float) -> float:
    dt = datetime.fromtimestamp(now, UTC)
    if scope == "month":
        return dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp()
    if scope != "day":
        raise ValueError("Budget scope must be day or month")
    return dt.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


class AdmissionError(Exception):
    """Stable, non-sensitive admission reason for the HTTP boundary."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class Reservation:
    id: str
    friend_id: int
    spend_id: int
    started_at: float
    expires_at: float
    model: str
    reserved_usd: float
    status: str


@dataclass(frozen=True)
class AccessRequest:
    id: int
    public_id: str
    name: str
    note: str
    status: str  # "pending" | "approved" | "denied"
    created_at: float
    decided_at: float | None


@dataclass(frozen=True)
class Friend:
    id: int
    request_id: int
    name: str
    budget_usd: float
    budget_scope: str
    spent_usd: float
    created_at: float
    revoked_at: float | None
    credential_version: int = 0

    @property
    def active(self) -> bool:
        return self.revoked_at is None


class RelayDB:
    def __init__(self, path: Path | str, clock: Callable[[], float] = time.time) -> None:
        self.path = str(path)
        self._clock = clock
        self._lock = threading.RLock()
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        self._db.execute("PRAGMA busy_timeout = 5000")
        self._db.execute("PRAGMA journal_mode = WAL")
        try:
            self._migrate()
        except BaseException:
            self._db.close()
            raise

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        # An instance lock alone cannot protect another worker's SQLite connection.
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                yield
                self._db.commit()
            except BaseException:
                self._db.rollback()
                raise

    def _migrate(self) -> None:
        with self._transaction():
            version = self._db.execute("PRAGMA user_version").fetchone()[0]
            if version == 0:
                for statement in _SCHEMA.split(";"):
                    if statement.strip():
                        self._db.execute(statement)
            if version < 2:
                self._db.execute(
                    "ALTER TABLE friends ADD COLUMN credential_version INTEGER NOT NULL DEFAULT 0"
                )
                for statement in _RESERVATIONS_SCHEMA.split(";"):
                    if statement.strip():
                        self._db.execute(statement)
            if version > SCHEMA_VERSION:
                raise RuntimeError(f"relay database is from a newer server (schema {version})")
            self._db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # ---- requests ----
    def create_request(self, name: str, note: str = "") -> AccessRequest:
        with self._lock:
            now = self._clock()
            public_id = new_public_id()
            self._db.execute(
                "INSERT INTO requests(public_id, name, note, status, created_at) "
                "VALUES (?, ?, ?, 'pending', ?)",
                (public_id, name.strip()[:80], note.strip()[:300], now),
            )
            self._db.commit()
            req = self.get_request(public_id)
            assert req is not None
            return req

    def get_request(self, public_id: str) -> AccessRequest | None:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM requests WHERE public_id = ?", (public_id,)
            ).fetchone()
        return self._request(row) if row else None

    def list_requests(self, status: str | None = None) -> list[AccessRequest]:
        with self._lock:
            if status is None:
                rows = self._db.execute(
                    "SELECT * FROM requests ORDER BY created_at DESC"
                ).fetchall()
            else:
                rows = self._db.execute(
                    "SELECT * FROM requests WHERE status = ? ORDER BY created_at DESC", (status,)
                ).fetchall()
        return [self._request(r) for r in rows]

    def approve_request(
        self, request_id: int, budget_usd: float, budget_scope: str
    ) -> tuple[Friend, str] | None:
        """Creates the friend record and a fresh token. Returns (friend, raw_token), or None if
        the request doesn't exist, isn't pending, or already has a friend (approved twice)."""
        validate_money(budget_usd)
        start_of_scope(budget_scope, self._clock())
        with self._transaction():
            req = self._db.execute(
                "SELECT * FROM requests WHERE id = ? AND status = 'pending'", (request_id,)
            ).fetchone()
            if req is None:
                return None
            raw = generate_token()
            now = self._clock()
            cur = self._db.execute(
                "INSERT INTO friends(request_id, name, token_hash, pending_token, budget_usd, "
                "budget_scope, spent_usd, created_at) VALUES (?, ?, ?, ?, ?, ?, 0.0, ?)",
                (request_id, req["name"], hash_token(raw), raw, budget_usd, budget_scope, now),
            )
            self._db.execute(
                "UPDATE requests SET status = 'approved', decided_at = ? WHERE id = ?",
                (now, request_id),
            )
            friend = self._friend(
                self._db.execute("SELECT * FROM friends WHERE id = ?", (cur.lastrowid,)).fetchone()
            )
            return friend, raw

    def deny_request(self, request_id: int) -> bool:
        with self._lock:
            cur = self._db.execute(
                "UPDATE requests SET status = 'denied', decided_at = ? "
                "WHERE id = ? AND status = 'pending'",
                (self._clock(), request_id),
            )
            self._db.commit()
            return cur.rowcount > 0

    def consume_pending_token(self, public_id: str) -> str | None:
        """The friend's app calls this (via the request-status endpoint) to collect its token.
        Delivered at most once: cleared from the database the moment it's read."""
        with self._transaction():
            row = self._db.execute(
                "SELECT f.id, f.pending_token FROM friends f "
                "JOIN requests r ON r.id = f.request_id WHERE r.public_id = ?",
                (public_id,),
            ).fetchone()
            if row is None or row["pending_token"] is None:
                return None
            self._db.execute("UPDATE friends SET pending_token = NULL WHERE id = ?", (row["id"],))
            return str(row["pending_token"])

    # ---- friends ----
    def get_friend(self, friend_id: int) -> Friend | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM friends WHERE id = ?", (friend_id,)).fetchone()
        return self._friend(row) if row else None

    def get_friend_by_token(self, raw_token: str) -> Friend | None:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM friends WHERE token_hash = ?", (hash_token(raw_token),)
            ).fetchone()
        return self._friend(row) if row else None

    def list_friends(self) -> list[Friend]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM friends ORDER BY created_at DESC").fetchall()
        return [self._friend(r) for r in rows]

    def revoke_friend(self, friend_id: int) -> bool:
        with self._lock:
            cur = self._db.execute(
                "UPDATE friends SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
                (self._clock(), friend_id),
            )
            self._db.commit()
            return cur.rowcount > 0

    def reissue_token(self, friend_id: int) -> str | None:
        """A fresh token for a friend who lost the old one (e.g. reinstalled). Old token stops
        working immediately (its hash is overwritten)."""
        with self._lock:
            friend = self._db.execute(
                "SELECT * FROM friends WHERE id = ? AND revoked_at IS NULL", (friend_id,)
            ).fetchone()
            if friend is None:
                return None
            raw = generate_token()
            self._db.execute(
                "UPDATE friends SET token_hash = ?, pending_token = ?, "
                "credential_version = credential_version + 1 WHERE id = ?",
                (hash_token(raw), raw, friend_id),
            )
            self._db.commit()
            return raw

    def set_budget(self, friend_id: int, budget_usd: float, budget_scope: str) -> bool:
        validate_money(budget_usd)
        start_of_scope(budget_scope, self._clock())
        with self._lock:
            cur = self._db.execute(
                "UPDATE friends SET budget_usd = ?, budget_scope = ? WHERE id = ?",
                (budget_usd, budget_scope, friend_id),
            )
            self._db.commit()
            return cur.rowcount > 0

    def add_spend(self, friend_id: int, usd: float) -> None:
        """Records a billable attempt. `spends` is the source of truth for scope-aware budget
        checks (spend_since); friends.spent_usd is a cheap cached all-time total for the dashboard,
        kept in the same transaction so it can never drift from the log."""
        validate_money(usd)
        with self._lock:
            now = self._clock()
            self._db.execute(
                "INSERT INTO spends(friend_id, ts, usd) VALUES (?, ?, ?)", (friend_id, now, usd)
            )
            self._db.execute(
                "UPDATE friends SET spent_usd = spent_usd + ? WHERE id = ?", (usd, friend_id)
            )
            self._db.commit()

    def spend_since(self, friend_id: int, since_ts: float) -> float:
        with self._lock:
            row = self._db.execute(
                "SELECT COALESCE(SUM(usd), 0) FROM spends WHERE friend_id = ? AND ts >= ?",
                (friend_id, since_ts),
            ).fetchone()
            return float(row[0])

    def reserve(
        self,
        friend_id: int,
        model: str,
        usd: float,
        max_inflight: int,
        lease_seconds: float = 180,
        credential_version: int | None = None,
    ) -> Reservation:
        """Atomically admit and book the entire upper bound before any provider call.

        The spend row includes active reservations, so other processes and periods see
        the same accounting. A crash leaves this charge in place until reconciliation.
        """
        validate_money(usd)
        if usd <= 0 or not 1 <= max_inflight <= 8 or not 1 <= lease_seconds <= 600:
            raise ValueError("Invalid reservation limits")
        with self._transaction():
            row = self._db.execute("SELECT * FROM friends WHERE id = ?", (friend_id,)).fetchone()
            if row is None or row["revoked_at"] is not None:
                raise AdmissionError("revoked")
            if credential_version is not None and row["credential_version"] != credential_version:
                raise AdmissionError("revoked")
            now = self._clock()
            # Expiry frees concurrency after a crash, never the reserved dollars.
            self._db.execute(
                "UPDATE reservations SET status = 'estimated' "
                "WHERE friend_id = ? AND status = 'active' AND expires_at <= ?",
                (friend_id, now),
            )
            try:
                validate_money(row["budget_usd"])
                since = start_of_scope(row["budget_scope"], now)
            except (TypeError, ValueError):
                raise AdmissionError("invalid") from None
            spends = self._db.execute(
                "SELECT usd FROM spends WHERE friend_id = ? AND ts >= ?", (friend_id, since)
            ).fetchall()
            try:
                for spend in spends:
                    validate_money(spend[0])
                total = math.fsum(spend[0] for spend in spends)
            except (TypeError, ValueError, OverflowError):
                raise AdmissionError("invalid") from None
            if math.fsum((total, usd)) > row["budget_usd"]:
                raise AdmissionError("budget")
            active = self._db.execute(
                "SELECT COUNT(*) FROM reservations WHERE friend_id = ? AND status = 'active'",
                (friend_id,),
            ).fetchone()[0]
            if active >= max_inflight:
                raise AdmissionError("busy")
            spend_id = self._db.execute(
                "INSERT INTO spends(friend_id, ts, usd) VALUES (?, ?, ?)", (friend_id, now, usd)
            ).lastrowid
            self._db.execute(
                "UPDATE friends SET spent_usd = spent_usd + ? WHERE id = ?", (usd, friend_id)
            )
            reservation_id = uuid4().hex
            self._db.execute(
                "INSERT INTO reservations VALUES (?, ?, ?, ?, ?, ?, ?, 'active')",
                (reservation_id, friend_id, spend_id, now, now + lease_seconds, model, usd),
            )
            result = self.get_reservation(reservation_id)
            assert result is not None
            return result

    def get_reservation(self, reservation_id: str) -> Reservation | None:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM reservations WHERE id = ?", (reservation_id,)
            ).fetchone()
            return Reservation(**dict(row)) if row else None

    def unsettled_reservations(self) -> list[Reservation]:
        """A bounded metadata-only owner view; never silently refund unresolved charges."""
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM reservations WHERE status IN ('active', 'estimated') "
                "ORDER BY started_at DESC LIMIT 100"
            ).fetchall()
            return [Reservation(**dict(row)) for row in rows]

    def settle(self, reservation_id: str, actual_usd: float | None) -> bool:
        """Reconcile once; None retains the upper bound as an estimated charge.

        A later trusted provider usage record can settle an estimated/crashed request.
        Settlement retains the spend timestamp from admission, even across midnight.
        """
        if actual_usd is not None:
            validate_money(actual_usd)
        return self._finish_reservation(
            reservation_id, actual_usd, "estimated" if actual_usd is None else "settled"
        )

    def release(self, reservation_id: str) -> bool:
        """Only for a provider rejection or proof that a request was never sent."""
        return self._finish_reservation(reservation_id, 0.0, "released")

    def _finish_reservation(self, reservation_id: str, usd: float | None, status: str) -> bool:
        with self._transaction():
            row = self._db.execute(
                "SELECT r.*, s.usd FROM reservations r JOIN spends s ON s.id = r.spend_id "
                "WHERE r.id = ?",
                (reservation_id,),
            ).fetchone()
            if row is None or row["status"] in {"settled", "released"}:
                return False
            if row["status"] == status:
                return False
            amount = row["usd"] if usd is None else usd
            self._db.execute("UPDATE spends SET usd = ? WHERE id = ?", (amount, row["spend_id"]))
            self._db.execute(
                "UPDATE friends SET spent_usd = "
                "(SELECT COALESCE(SUM(usd), 0) FROM spends WHERE friend_id = ?) WHERE id = ?",
                (row["friend_id"], row["friend_id"]),
            )
            self._db.execute(
                "UPDATE reservations SET status = ? WHERE id = ?", (status, reservation_id)
            )
            return True

    # ---- mapping helpers ----
    @staticmethod
    def _request(row: sqlite3.Row) -> AccessRequest:
        return AccessRequest(**{k: row[k] for k in row.keys()})

    @staticmethod
    def _friend(row: sqlite3.Row) -> Friend:
        d = {k: row[k] for k in row.keys() if k not in ("token_hash", "pending_token")}
        return Friend(**d)
