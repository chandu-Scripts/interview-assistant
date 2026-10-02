import json
import logging
import re
import sqlite3
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

log = logging.getLogger(__name__)

SCHEMA_VERSION = 2
Status = Literal["complete", "interrupted", "stopped", "error"]
ExportFormat = Literal["md", "json"]

_SCHEMA = """
CREATE TABLE sessions (
    id INTEGER PRIMARY KEY,
    started_at REAL NOT NULL,
    ended_at REAL,
    title TEXT NOT NULL DEFAULT ''
);
CREATE TABLE turns (
    id INTEGER PRIMARY KEY,
    session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    ts REAL NOT NULL,
    question TEXT NOT NULL,
    answer TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'typed',
    status TEXT NOT NULL DEFAULT 'complete',
    category TEXT NOT NULL DEFAULT '',
    agent TEXT NOT NULL DEFAULT '',
    tier TEXT NOT NULL DEFAULT '',
    confidence REAL,
    decision_source TEXT NOT NULL DEFAULT '',
    route TEXT NOT NULL DEFAULT '',
    first_token_s REAL,
    total_s REAL,
    cost_usd REAL,
    error TEXT NOT NULL DEFAULT '',
    corrected_from TEXT NOT NULL DEFAULT ''
);
CREATE INDEX turns_session ON turns(session_id, ts);
CREATE VIRTUAL TABLE turns_fts USING fts5(
    question, answer, content='turns', content_rowid='id', tokenize='porter unicode61'
);
CREATE TRIGGER turns_ai AFTER INSERT ON turns BEGIN
    INSERT INTO turns_fts(rowid, question, answer) VALUES (new.id, new.question, new.answer);
END;
CREATE TRIGGER turns_ad AFTER DELETE ON turns BEGIN
    INSERT INTO turns_fts(turns_fts, rowid, question, answer)
    VALUES ('delete', old.id, old.question, old.answer);
END;
CREATE TRIGGER turns_au AFTER UPDATE ON turns BEGIN
    INSERT INTO turns_fts(turns_fts, rowid, question, answer)
    VALUES ('delete', old.id, old.question, old.answer);
    INSERT INTO turns_fts(rowid, question, answer) VALUES (new.id, new.question, new.answer);
END;
"""


@dataclass(frozen=True)
class TurnRecord:
    question: str
    answer: str = ""
    source: str = "typed"
    status: Status = "complete"
    category: str = ""
    agent: str = ""
    tier: str = ""
    confidence: float | None = None
    decision_source: str = ""
    route: str = ""
    first_token_s: float | None = None
    total_s: float | None = None
    cost_usd: float | None = None
    error: str = ""
    # When the question was asked (defaults to "now" at save time). An answer that is cancelled
    # finishes saving AFTER the newer question's answer, so order must follow the question.
    asked_at: float | None = None
    # Non-empty when the user corrected Jev's classification: the ORIGINAL category value, so a
    # regenerate-with-the-same-category redo is not mistaken for a correction.
    corrected_from: str = ""


@dataclass(frozen=True)
class StoredTurn(TurnRecord):
    id: int = 0
    session_id: int = 0
    ts: float = 0.0


@dataclass(frozen=True)
class SessionInfo:
    id: int
    started_at: float
    ended_at: float | None
    title: str
    turns: int
    cost_usd: float


@dataclass(frozen=True)
class SearchHit:
    turn: StoredTurn
    snippet: str  # matching excerpt with [brackets] around the hit


def fts_query(text: str) -> str | None:
    """Turn free user text into a safe FTS5 query: every word must match, last one as a prefix."""
    words = re.findall(r"\w+", text, re.UNICODE)
    if not words:
        return None
    terms = [f'"{w}"' for w in words]
    terms[-1] += "*"
    return " ".join(terms)


class HistoryStore:
    """Local, private conversation history (SQLite + full-text search).

    Thread-safe. `enabled` can be switched off at runtime (e.g. by a privacy mode) - while off,
    nothing is written. Deleted rows are overwritten (secure_delete) and the file is compacted.
    """

    def __init__(
        self,
        path: Path | str,
        enabled: bool = True,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.path = str(path)
        self.enabled = enabled  # the user's saving preference
        self.suspended = False  # temporary override (incognito): nothing is written
        self._clock = clock
        self._lock = threading.RLock()
        self.recovered_from: Path | None = None  # set when a damaged database was set aside
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        try:
            self._db = self._open()
        except sqlite3.DatabaseError as e:
            if self.path == ":memory:":
                raise
            self.recovered_from = self._quarantine(e)
            self._db = self._open()  # a fresh, empty database
        try:  # make FTS index deletions overwrite the old terms too
            self._db.execute("INSERT INTO turns_fts(turns_fts, rank) VALUES('secure-delete', 1)")
            self._db.commit()
        except sqlite3.Error:
            log.warning("FTS5 secure-delete unavailable; deletions rely on compaction")
        self._session_id: int | None = None

    def _open(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, check_same_thread=False)
        try:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys = ON")
            db.execute("PRAGMA secure_delete = ON")
            self._db = db
            if self.path != ":memory:":
                # Fast structural check: finds truncated/garbled files at startup, not mid-answer.
                row = db.execute("PRAGMA quick_check").fetchone()
                if row is None or row[0] != "ok":
                    raise sqlite3.DatabaseError(f"integrity check failed: {row[0] if row else '?'}")
            self._migrate()
        except sqlite3.DatabaseError:
            db.close()
            raise
        return db

    def _quarantine(self, error: Exception) -> Path:
        """Move a damaged database (and its journal files) aside instead of failing to start."""
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(self._clock()))
        target = Path(self.path).with_name(f"{Path(self.path).name}.corrupt-{stamp}")
        log.error("History database is damaged (%s); moving it to %s", error, target.name)
        for suffix in ("", "-journal", "-wal", "-shm"):
            src = Path(self.path + suffix)
            if src.exists():
                src.replace(target if suffix == "" else Path(str(target) + suffix))
        return target

    def _migrate(self) -> None:
        with self._lock:
            version = self._db.execute("PRAGMA user_version").fetchone()[0]
            if version == 0:
                self._db.executescript(_SCHEMA)
                self._db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
                self._db.commit()
                return
            if version == 1:
                self._db.execute(
                    "ALTER TABLE turns ADD COLUMN corrected_from TEXT NOT NULL DEFAULT ''"
                )
                self._db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
                self._db.commit()
                version = SCHEMA_VERSION
            if version > SCHEMA_VERSION:
                raise RuntimeError(f"history database is from a newer app (schema {version})")

    def close(self) -> None:
        with self._lock:
            self.end_session()
            self._db.close()

    # ---- writing ----
    @property
    def writable(self) -> bool:
        return self.enabled and not self.suspended

    @property
    def current_session(self) -> int | None:
        return self._session_id

    def end_session(self) -> None:
        """Close the current session; the next turn opens a new one."""
        with self._lock:
            if self._session_id is not None:
                self._db.execute(
                    "UPDATE sessions SET ended_at = ? WHERE id = ?",
                    (self._clock(), self._session_id),
                )
                self._db.commit()
            self._session_id = None

    def add_turn(self, turn: TurnRecord) -> int | None:
        """Store a turn (opening a session lazily). Returns its id, or None when disabled."""
        if not self.writable or not turn.question.strip():
            return None
        with self._lock:
            now = self._clock()
            if self._session_id is None:
                cur = self._db.execute(
                    "INSERT INTO sessions(started_at, title) VALUES (?, ?)",
                    (now, turn.question.strip()[:60]),
                )
                self._session_id = int(cur.lastrowid or 0)
            d = asdict(turn)
            asked_at = d.pop("asked_at") or now
            cols = ", ".join(d)
            marks = ", ".join("?" for _ in d)
            cur = self._db.execute(
                f"INSERT INTO turns(session_id, ts, {cols}) VALUES (?, ?, {marks})",
                (self._session_id, asked_at, *d.values()),
            )
            self._db.commit()
            return int(cur.lastrowid or 0)

    # ---- reading ----
    def list_sessions(self, limit: int = 200) -> list[SessionInfo]:
        with self._lock:
            rows = self._db.execute(
                """SELECT s.id, s.started_at, s.ended_at, s.title, COUNT(t.id) AS n,
                          COALESCE(SUM(t.cost_usd), 0) AS cost
                   FROM sessions s LEFT JOIN turns t ON t.session_id = s.id
                   GROUP BY s.id ORDER BY s.started_at DESC, s.id DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        return [SessionInfo(r[0], r[1], r[2], r[3], r[4], r[5]) for r in rows]

    def get_turns(self, session_id: int) -> list[StoredTurn]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM turns WHERE session_id = ? ORDER BY ts, id", (session_id,)
            ).fetchall()
        return [self._turn(r) for r in rows]

    def search(self, text: str, limit: int = 50) -> list[SearchHit]:
        q = fts_query(text)
        if q is None:
            return []
        with self._lock:
            rows = self._db.execute(
                """SELECT t.*, snippet(turns_fts, -1, '[', ']', '…', 12) AS snip
                   FROM turns_fts JOIN turns t ON t.id = turns_fts.rowid
                   WHERE turns_fts MATCH ? ORDER BY rank LIMIT ?""",
                (q, limit),
            ).fetchall()
        return [SearchHit(self._turn(r), r["snip"]) for r in rows]

    @staticmethod
    def _turn(r: sqlite3.Row) -> StoredTurn:
        d = {k: r[k] for k in r.keys() if k != "snip"}
        return StoredTurn(**d)

    def counts(self) -> tuple[int, int]:
        with self._lock:
            s = self._db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
            t = self._db.execute("SELECT COUNT(*) FROM turns").fetchone()[0]
        return int(s), int(t)

    # ---- deleting ----
    def delete_turn(self, turn_id: int) -> None:
        with self._lock:
            row = self._db.execute(
                "SELECT session_id FROM turns WHERE id = ?", (turn_id,)
            ).fetchone()
            self._db.execute("DELETE FROM turns WHERE id = ?", (turn_id,))
            if row is not None:  # drop a session that just became empty
                left = self._db.execute(
                    "SELECT COUNT(*) FROM turns WHERE session_id = ?", (row[0],)
                ).fetchone()[0]
                if left == 0:
                    self._db.execute("DELETE FROM sessions WHERE id = ?", (row[0],))
                    if self._session_id == row[0]:
                        self._session_id = None
            self._db.commit()

    def delete_session(self, session_id: int) -> None:
        with self._lock:
            self._db.execute("DELETE FROM turns WHERE session_id = ?", (session_id,))
            self._db.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            if self._session_id == session_id:
                self._session_id = None
            self._db.commit()

    def delete_all(self) -> None:
        with self._lock:
            self._db.execute("DELETE FROM turns")
            self._db.execute("DELETE FROM sessions")
            self._db.execute("INSERT INTO turns_fts(turns_fts) VALUES ('rebuild')")
            self._session_id = None
            self._db.commit()
            self._compact()

    def purge_older_than(self, days: float) -> int:
        """Retention: delete sessions older than `days`. Returns how many were removed."""
        cutoff = self._clock() - days * 86400
        with self._lock:
            ids = [
                r[0]
                for r in self._db.execute("SELECT id FROM sessions WHERE started_at < ?", (cutoff,))
                if r[0] != self._session_id
            ]
            for sid in ids:
                self._db.execute("DELETE FROM turns WHERE session_id = ?", (sid,))
                self._db.execute("DELETE FROM sessions WHERE id = ?", (sid,))
            self._db.commit()
            if ids:
                self._compact()
        return len(ids)

    def _compact(self) -> None:
        try:
            self._db.execute("INSERT INTO turns_fts(turns_fts) VALUES ('optimize')")
            self._db.commit()
            self._db.execute("VACUUM")
        except sqlite3.Error:
            log.exception("Could not compact history database")

    # ---- export ----
    def export_session(self, session_id: int, fmt: ExportFormat = "md") -> str:
        info = next((s for s in self.list_sessions(10_000) if s.id == session_id), None)
        if info is None:
            raise KeyError(f"no such session: {session_id}")
        turns = self.get_turns(session_id)
        return _to_json([(info, turns)]) if fmt == "json" else _to_markdown([(info, turns)])

    def export_all(self, fmt: ExportFormat = "md") -> str:
        data = [(s, self.get_turns(s.id)) for s in reversed(self.list_sessions(100_000))]
        return _to_json(data) if fmt == "json" else _to_markdown(data)


def _stamp(ts: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))


def _to_markdown(data: list[tuple[SessionInfo, list[StoredTurn]]]) -> str:
    out: list[str] = []
    for info, turns in data:
        out.append(f"# Interview session - {_stamp(info.started_at)}")
        out.append(f"{info.turns} question(s), estimated cost ${info.cost_usd:.4f}\n")
        for t in turns:
            out.append(f"## Q: {t.question.strip()}")
            corrected = f"corrected from {t.corrected_from}" if t.corrected_from else ""
            meta = " | ".join(x for x in (t.category, corrected, t.route, t.status) if x)
            if meta:
                out.append(f"*{meta}*\n")
            out.append(t.answer.strip() or "_(no answer)_")
            if t.error:
                out.append(f"\n> Error: {t.error}")
            out.append("")
    return "\n".join(out).rstrip() + "\n"


def _to_json(data: list[tuple[SessionInfo, list[StoredTurn]]]) -> str:
    payload = [
        {"session": asdict(info), "turns": [asdict(t) for t in turns]} for info, turns in data
    ]
    return json.dumps(payload, indent=2, ensure_ascii=False)
