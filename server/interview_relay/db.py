"""SQLite storage for access requests and approved friends.

Metadata only, matching the desktop app's own privacy rule (interview_assistant.cost.tracker's
UsageStore): this database never sees a prompt or an answer, only who was approved, their budget,
and how much of it they've spent. Thread-safe the same way interview_assistant.history.store is:
one connection, a lock, check_same_thread=False (FastAPI runs sync handlers in a thread pool)."""

import sqlite3
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from interview_relay.auth import generate_token, hash_token, new_public_id

SCHEMA_VERSION = 1

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
        self._migrate()

    def _migrate(self) -> None:
        with self._lock:
            version = self._db.execute("PRAGMA user_version").fetchone()[0]
            if version == 0:
                self._db.executescript(_SCHEMA)
                self._db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
                self._db.commit()
            elif version > SCHEMA_VERSION:
                raise RuntimeError(f"relay database is from a newer server (schema {version})")

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
        with self._lock:
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
            self._db.commit()
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
        with self._lock:
            row = self._db.execute(
                "SELECT f.id, f.pending_token FROM friends f "
                "JOIN requests r ON r.id = f.request_id WHERE r.public_id = ?",
                (public_id,),
            ).fetchone()
            if row is None or row["pending_token"] is None:
                return None
            self._db.execute("UPDATE friends SET pending_token = NULL WHERE id = ?", (row["id"],))
            self._db.commit()
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
                "UPDATE friends SET token_hash = ?, pending_token = ? WHERE id = ?",
                (hash_token(raw), raw, friend_id),
            )
            self._db.commit()
            return raw

    def set_budget(self, friend_id: int, budget_usd: float, budget_scope: str) -> bool:
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

    # ---- mapping helpers ----
    @staticmethod
    def _request(row: sqlite3.Row) -> AccessRequest:
        return AccessRequest(**{k: row[k] for k in row.keys()})

    @staticmethod
    def _friend(row: sqlite3.Row) -> Friend:
        d = {k: row[k] for k in row.keys() if k not in ("token_hash", "pending_token")}
        return Friend(**d)
