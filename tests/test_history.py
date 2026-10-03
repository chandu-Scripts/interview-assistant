import json
import sqlite3
import threading

import pytest
from PySide6.QtCore import Qt

from interview_assistant.agents.jev import JevConfig
from interview_assistant.app.controller import AssistantController
from interview_assistant.app.interview_session import InterviewSessionManager
from interview_assistant.cost.pricing import PriceBook
from interview_assistant.cost.tracker import Budget, CostTracker
from interview_assistant.history.store import SCHEMA_VERSION, HistoryStore, TurnRecord, fts_query
from interview_assistant.llm.router import RouteEvent
from interview_assistant.ui.history_window import HistoryWindow
from tests.test_app import FakeJev


class Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def turn(q="What is a mutex?", a="A lock.", **kw):
    return TurnRecord(question=q, answer=a, **kw)


@pytest.fixture
def store():
    s = HistoryStore(":memory:")
    yield s
    s.close()


# ---------------- store basics ----------------
def test_add_and_read_back(store):
    tid = store.add_turn(
        turn(category="concept", agent="concept", tier="fast", confidence=0.9, cost_usd=0.001)
    )
    assert tid
    (s,) = store.list_sessions()
    assert s.turns == 1 and s.cost_usd == pytest.approx(0.001) and s.title == "What is a mutex?"
    (t,) = store.get_turns(s.id)
    assert (t.question, t.answer, t.category, t.confidence) == (
        "What is a mutex?",
        "A lock.",
        "concept",
        0.9,
    )
    assert t.id == tid and t.session_id == s.id and t.status == "complete"


def test_session_opened_lazily_and_ended(store):
    assert store.list_sessions() == [] and store.current_session is None
    store.add_turn(turn("q1"))
    store.add_turn(turn("q2"))
    assert len(store.list_sessions()) == 1
    store.end_session()
    store.add_turn(turn("q3"))
    sessions = store.list_sessions()
    assert len(sessions) == 2 and sessions[0].title == "q3" and sessions[1].ended_at is not None


def test_long_title_truncated_and_empty_question_ignored(store):
    store.add_turn(turn("x" * 200))
    assert len(store.list_sessions()[0].title) == 60
    assert store.add_turn(turn("   ")) is None and store.counts() == (1, 1)


def test_disabled_store_writes_nothing(store):
    store.enabled = False
    assert store.add_turn(turn()) is None
    assert store.counts() == (0, 0)
    store.enabled = True
    assert store.add_turn(turn())


# ---------------- interview sessions (Step 17: "Create Interview Session") ----------------
def test_create_get_list_and_end_interview_session(store):
    assert store.list_interview_sessions() == []
    sid = store.create_interview_session(
        company="Google",
        role_title="Software Engineer",
        job_description="Build search infra.",
        resume_override="5y Python",
        ai_instructions="Be concise.",
        answer_format="Bullet points",
        answer_length="Short",
        answer_tone="Formal",
        model_route_override="openai:gpt-4o",
    )
    assert sid
    got = store.get_interview_session(sid)
    assert got is not None
    assert (got.company, got.role_title, got.job_description) == (
        "Google",
        "Software Engineer",
        "Build search infra.",
    )
    assert got.ended_at is None and got.duration_s is None
    assert [s.id for s in store.list_interview_sessions()] == [sid]

    store.end_interview_session(sid)
    ended = store.get_interview_session(sid)
    assert ended is not None
    assert ended.ended_at is not None and ended.duration_s is not None and ended.duration_s >= 0


def test_get_interview_session_missing_returns_none(store):
    assert store.get_interview_session(999) is None


def test_interview_sessions_newest_first(store):
    a = store.create_interview_session(company="A")
    b = store.create_interview_session(company="B")
    assert [s.id for s in store.list_interview_sessions()] == [b, a]


def test_active_interview_session_tags_new_turn_sessions(store):
    """set_active_interview_session() only affects a NEWLY opened turn-session, not one already
    in progress - matches add_turn()'s existing lazy-open behavior."""
    store.add_turn(turn("before activation"))  # opens a turn-session with no interview_session_id
    iid = store.create_interview_session(company="Acme")
    store.set_active_interview_session(iid)
    store.add_turn(turn("still in the same open turn-session"))
    store.end_session()  # close it so the next add_turn opens a fresh, now-tagged one

    store.add_turn(turn("after activation"))
    sessions = store.list_sessions()
    untagged = next(s for s in sessions if s.title == "before activation")
    tagged = next(s for s in sessions if s.title == "after activation")
    # SessionInfo doesn't expose interview_session_id directly; check via the raw turns table.
    row_untagged = store._db.execute(
        "SELECT interview_session_id FROM sessions WHERE id = ?", (untagged.id,)
    ).fetchone()
    row_tagged = store._db.execute(
        "SELECT interview_session_id FROM sessions WHERE id = ?", (tagged.id,)
    ).fetchone()
    assert row_untagged[0] is None
    assert row_tagged[0] == iid

    store.set_active_interview_session(None)
    store.end_session()
    store.add_turn(turn("after deactivation"))
    deactivated = next(s for s in store.list_sessions() if s.title == "after deactivation")
    row = store._db.execute(
        "SELECT interview_session_id FROM sessions WHERE id = ?", (deactivated.id,)
    ).fetchone()
    assert row[0] is None


_V2_SCHEMA = """
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
"""


def test_migration_from_v2_adds_interview_sessions_table_and_column(tmp_path):
    """A database created by the pre-Step-17 app (no interview_sessions table, no
    interview_session_id column) must open and gain both, without disturbing existing data."""
    path = tmp_path / "h.db"
    raw = sqlite3.connect(path)
    raw.executescript(_V2_SCHEMA)
    raw.execute("INSERT INTO sessions(started_at, title) VALUES (1000.0, 'old session')")
    raw.execute(
        "INSERT INTO turns(session_id, ts, question, answer) VALUES (1, 1000.0, 'old q', 'old a')"
    )
    raw.execute("PRAGMA user_version = 2")
    raw.commit()
    raw.close()

    store = HistoryStore(path)
    assert store.recovered_from is None  # a version upgrade is not "damage"
    (existing,) = store.get_turns(1)
    assert existing.question == "old q"  # pre-existing data survived the migration
    sid = store.create_interview_session(company="Acme")  # the new table works
    assert store.get_interview_session(sid) is not None
    assert store.list_interview_sessions() == [store.get_interview_session(sid)]
    raw2 = sqlite3.connect(path)
    assert raw2.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    store.close()


def test_reopen_persists_and_migration_is_idempotent(tmp_path):
    path = tmp_path / "h.db"
    s1 = HistoryStore(path)
    s1.add_turn(turn())
    s1.close()
    s2 = HistoryStore(path)
    assert s2.counts() == (1, 1)
    assert s2.search("mutex")
    s2.close()


_V1_SCHEMA = """
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
    error TEXT NOT NULL DEFAULT ''
);
CREATE INDEX turns_session ON turns(session_id, ts);
CREATE VIRTUAL TABLE turns_fts USING fts5(
    question, answer, content='turns', content_rowid='id', tokenize='porter unicode61'
);
CREATE TRIGGER turns_ai AFTER INSERT ON turns BEGIN
    INSERT INTO turns_fts(rowid, question, answer) VALUES (new.id, new.question, new.answer);
END;
"""


def test_migration_from_v1_adds_corrected_from_column(tmp_path):
    """A database created by the pre-correction-feature app must open and gain the new column."""
    path = tmp_path / "h.db"
    raw = sqlite3.connect(path)
    raw.executescript(_V1_SCHEMA)
    raw.execute("INSERT INTO sessions(started_at, title) VALUES (1000.0, 'old session')")
    raw.execute(
        "INSERT INTO turns(session_id, ts, question, answer) VALUES (1, 1000.0, 'old q', 'old a')"
    )
    raw.execute("PRAGMA user_version = 1")
    raw.commit()
    raw.close()

    store = HistoryStore(path)
    assert store.recovered_from is None  # a version upgrade is not "damage"
    (existing,) = store.get_turns(1)
    assert existing.question == "old q" and existing.corrected_from == ""  # backfilled default
    # add_turn() doesn't resume the old session (nothing tracked it as open) - it opens a new one.
    tid = store.add_turn(turn("new q", corrected_from="hr"))
    assert tid
    new_session = next(s for s in store.list_sessions() if s.id != 1)
    assert store.get_turns(new_session.id)[-1].corrected_from == "hr"
    raw2 = sqlite3.connect(path)
    assert raw2.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    raw2.close()
    store.close()

    reopened = HistoryStore(path)  # migrating twice must be a no-op, not an error
    assert reopened.counts() == (2, 2) and reopened.recovered_from is None
    reopened.close()


def test_correction_recorded_and_exported(store):
    store.add_turn(turn("Find the second highest salary?", "wrong: SELECT...", category="coding"))
    store.add_turn(
        turn(
            "Find the second highest salary?",
            "SELECT MAX(salary) ...",
            category="data_sql",
            decision_source="user",
            corrected_from="coding",
        )
    )
    sid = store.list_sessions()[0].id
    t1, t2 = store.get_turns(sid)
    assert t1.corrected_from == "" and t2.corrected_from == "coding"
    md = store.export_session(sid, "md")
    assert "corrected from coding" in md
    data = json.loads(store.export_session(sid, "json"))
    assert data[0]["turns"][1]["corrected_from"] == "coding"


def test_newer_schema_is_refused(tmp_path):
    path = tmp_path / "h.db"
    HistoryStore(path).close()
    c = sqlite3.connect(path)
    c.execute("PRAGMA user_version = 99")
    c.commit()
    c.close()
    with pytest.raises(RuntimeError):
        HistoryStore(path)


def test_concurrent_writers(store):
    def work(n):
        for i in range(25):
            store.add_turn(turn(f"question {n}-{i}"))

    threads = [threading.Thread(target=work, args=(n,)) for n in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert store.counts() == (1, 100)


# ---------------- search ----------------
def test_search_matches_question_or_answer_with_stemming_and_prefix(store):
    store.add_turn(
        turn("Tell me about a conflict on your team", "I mediated between two engineers.")
    )
    store.add_turn(turn("What is a mutex?", "A lock protecting shared memory."))
    assert [h.turn.question for h in store.search("conflicts")] == [
        "Tell me about a conflict on your team"
    ]
    assert len(store.search("mediat")) == 1  # prefix, matches answer text
    assert len(store.search("shared memory")) == 1  # all words must match
    assert store.search("conflict mutex") == []
    hit = store.search("mutex")[0]
    assert "[mutex]" in hit.snippet.lower() or "[Mutex]" in hit.snippet


@pytest.mark.parametrize(
    "q",
    ['"', "AND", "OR NOT", "foo*bar", "a:b", "NEAR(", "-", "'; DROP TABLE turns; --", "()", "\\"],
)
def test_hostile_search_input_never_crashes(store, q):
    store.add_turn(turn())
    store.search(q)  # must not raise
    assert store.counts() == (1, 1)


def test_fts_query_builder():
    assert fts_query("  ") is None and fts_query("!!!") is None
    assert fts_query("hash map") == '"hash" "map"*'


# ---------------- deletion ----------------
def test_delete_turn_removes_from_search_and_drops_empty_session(store):
    a = store.add_turn(turn("alpha question", "alpha answer"))
    store.add_turn(turn("beta question", "beta answer"))
    store.delete_turn(a)
    assert store.search("alpha") == [] and len(store.search("beta")) == 1
    assert store.counts() == (1, 1)
    (t,) = store.get_turns(store.list_sessions()[0].id)
    store.delete_turn(t.id)  # last turn: the empty session disappears too
    assert store.counts() == (0, 0) and store.current_session is None


def test_delete_session_and_all(store):
    store.add_turn(turn("one"))
    store.end_session()
    store.add_turn(turn("two"))
    sid = store.list_sessions()[1].id
    store.delete_session(sid)
    assert [s.title for s in store.list_sessions()] == ["two"]
    store.delete_all()
    assert store.counts() == (0, 0) and store.search("two") == []
    store.add_turn(turn("fresh start"))  # still usable afterwards
    assert store.counts() == (1, 1)


def test_deleted_text_is_really_gone_from_the_file(tmp_path):
    path = tmp_path / "h.db"
    s = HistoryStore(path)
    marker = "zxqvsecretmarker"
    s.add_turn(turn(f"question about {marker}", f"answer mentioning {marker} twice {marker}"))
    s.add_turn(turn("keeper question", "keeper answer"))
    s.close()
    assert marker.encode() in path.read_bytes()  # sanity: it was stored
    s = HistoryStore(path)
    hit = s.search(marker)[0]
    s.delete_turn(hit.turn.id)
    s.delete_all()  # compacts (VACUUM)
    s.close()
    assert marker.encode() not in path.read_bytes()


def test_deleting_one_session_scrubs_its_text_without_full_vacuum(tmp_path):
    path = tmp_path / "h.db"
    s = HistoryStore(path)
    s.add_turn(turn("plain", "zzuniquesessionword everywhere"))
    sid = s.current_session
    s.end_session()
    s.add_turn(turn("other", "other"))
    s.delete_session(sid)
    s.close()
    assert b"zzuniquesessionword" not in path.read_bytes()


def test_retention_purge_keeps_recent_and_current(tmp_path):
    clk = Clock()
    s = HistoryStore(tmp_path / "h.db", clock=clk)
    s.add_turn(turn("ancient"))
    s.end_session()
    clk.t += 86400 * 10
    s.add_turn(turn("recent"))
    s.end_session()
    clk.t += 86400 * 25
    s.add_turn(turn("current"))
    assert s.purge_older_than(30) == 1  # only "ancient" is >30 days old
    assert [x.title for x in s.list_sessions()] == ["current", "recent"]
    clk.t += 86400 * 100
    assert s.purge_older_than(30) == 1  # "recent" goes; the open session is protected
    assert [x.title for x in s.list_sessions()] == ["current"]
    s.close()


# ---------------- export ----------------
def test_export_markdown_and_json(store):
    store.add_turn(turn("Q one?", "Answer **one**", category="coding", route="openai:gpt-4o"))
    store.add_turn(turn("Q two?", "", status="error", error="All providers failed"))
    sid = store.list_sessions()[0].id
    md = store.export_session(sid, "md")
    assert "## Q: Q one?" in md and "Answer **one**" in md and "coding | openai:gpt-4o" in md
    assert "_(no answer)_" in md and "Error: All providers failed" in md
    data = json.loads(store.export_session(sid, "json"))
    assert data[0]["session"]["id"] == sid and len(data[0]["turns"]) == 2
    assert data[0]["turns"][0]["route"] == "openai:gpt-4o"
    store.end_session()
    store.add_turn(turn("Later session"))
    assert len(json.loads(store.export_all("json"))) == 2
    assert "Later session" in store.export_all("md")
    with pytest.raises(KeyError):
        store.export_session(9999)


# ---------------- controller integration ----------------
@pytest.fixture
def stack(qtbot):
    made = []

    def make(jev=None, tracker=None, history=None):
        h = history or HistoryStore(":memory:")
        c = AssistantController(jev or FakeJev(), poll_ms=10, tracker=tracker, history=h)
        made.append(c)
        return c, h

    yield make
    for c in made:
        c.shutdown()


def test_answered_question_saved_with_metadata(qtbot, stack):
    tracker = CostTracker(PriceBook())
    c, h = stack(tracker=tracker)
    c.ask("What is a mutex?", "voice")
    qtbot.waitUntil(lambda: h.counts()[1] == 1, timeout=3000)
    t = h.get_turns(h.list_sessions()[0].id)[0]
    assert (t.question, t.answer, t.source, t.status) == (
        "What is a mutex?",
        "Hello",
        "voice",
        "complete",
    )
    assert (t.category, t.agent, t.tier, t.decision_source) == ("coding", "x", "fast", "rules")
    assert t.confidence == 0.9 and t.route == "p:m" and t.first_token_s is not None
    assert t.total_s is not None and t.cost_usd == 0.0


def test_filler_is_not_saved(qtbot, stack):
    c, h = stack()
    c.ask("Okay, great.", "voice")
    qtbot.wait(300)
    assert h.counts() == (0, 0)


def test_interrupted_and_stopped_answers_are_saved_with_partial_text(qtbot, stack):
    jev = FakeJev(chunks=("a", "b", "c", "d"), hold=0.8)
    c, h = stack(jev)
    c.ask("First question here?")
    qtbot.waitUntil(lambda: jev.asked == ["First question here?"], timeout=3000)
    qtbot.wait(100)
    c.ask("Second question here?")  # interrupts the first
    qtbot.waitUntil(lambda: h.counts()[1] == 2 or len(jev.asked) == 2, timeout=15000)
    qtbot.waitUntil(lambda: h.counts()[1] == 2, timeout=15000)
    first, second = h.get_turns(h.list_sessions()[0].id)
    assert (first.status, second.status) == ("interrupted", "complete")
    assert first.answer.startswith("a") and second.answer == "abcd"

    c.ask("Third question here?")
    qtbot.waitUntil(lambda: jev.asked[-1] == "Third question here?", timeout=3000)
    qtbot.wait(100)
    c.stop()
    qtbot.waitUntil(lambda: h.counts()[1] == 3, timeout=5000)
    assert h.get_turns(h.list_sessions()[0].id)[2].status == "stopped"


def test_provider_error_is_saved(qtbot, stack):
    from interview_assistant.llm.router import NoRoutesAvailable

    c, h = stack(FakeJev(fail=NoRoutesAvailable("x")))
    c.ask("What is a mutex?")
    qtbot.waitUntil(lambda: h.counts()[1] == 1, timeout=3000)
    t = h.get_turns(h.list_sessions()[0].id)[0]
    assert t.status == "error" and "API key" in t.error and t.answer == ""


def test_budget_block_is_saved(qtbot, stack):
    tracker = CostTracker(PriceBook(), budget=Budget(1e-9, hard_stop=True))
    tracker.record_stt("openai", "whisper-1", 60)
    c, h = stack(tracker=tracker)
    c.ask("What is a mutex?")
    qtbot.waitUntil(lambda: h.counts()[1] == 1, timeout=3000)
    assert "Budget reached" in h.get_turns(h.list_sessions()[0].id)[0].error


def test_new_interview_starts_new_history_session(qtbot, stack):
    c, h = stack()
    c.ask("What is a mutex?")
    qtbot.waitUntil(lambda: h.counts()[1] == 1, timeout=3000)
    c.new_interview()
    c.ask("What is a semaphore?")
    qtbot.waitUntil(lambda: h.counts()[1] == 2, timeout=3000)
    assert h.counts()[0] == 2


def test_storage_failure_never_breaks_answering(qtbot, stack):
    c, h = stack()
    seen = []
    c.answer_finished.connect(lambda *a: seen.append(a))
    h._db.close()  # simulate a broken database
    c.ask("What is a mutex?")
    qtbot.waitUntil(lambda: bool(seen), timeout=3000)


def test_disabled_history_saves_nothing(qtbot, stack):
    h = HistoryStore(":memory:", enabled=False)
    c, _ = stack(history=h)
    done = []
    c.answer_finished.connect(lambda *a: done.append(1))
    c.ask("What is a mutex?")
    qtbot.waitUntil(lambda: bool(done), timeout=3000)
    assert h.counts() == (0, 0)


# ---------------- history window ----------------
class Prompts:
    def __init__(self, answer=True, path=None):
        self.answer, self.path, self.asked = answer, path, []

    def confirm(self, text):
        self.asked.append(text)
        return self.answer

    def pick(self, name):
        return self.path


def make_window(qtbot, store, session_manager=None, **kw):
    p = Prompts(**kw)
    w = HistoryWindow(store, p.confirm, p.pick, session_manager=session_manager)
    qtbot.addWidget(w)
    return w, p


def filled(store):
    store.add_turn(
        turn("Tell me about a conflict", "I mediated.", category="behavioral", cost_usd=0.002)
    )
    store.add_turn(turn("What is a mutex?", "A lock."))
    store.end_session()
    store.add_turn(turn("Design a URL shortener", "Use a hash."))
    return store


def test_window_lists_sessions_and_turns(qtbot, store):
    w, _ = make_window(qtbot, filled(store))
    assert w.sessions.count() == 2 and w.turns.count() == 1  # newest session first
    assert "URL shortener" in w.detail.toPlainText()
    w.sessions.setCurrentRow(1)
    assert w.turns.count() == 2
    w.turns.setCurrentRow(1)
    assert "A lock." in w.detail.toPlainText()


def test_window_search_and_clear(qtbot, store):
    w, _ = make_window(qtbot, filled(store))
    qtbot.keyClicks(w.search, "conflict")
    qtbot.waitUntil(
        lambda: w.turns.count() == 1 and "conflict" in w.turns.item(0).text(), timeout=2000
    )
    assert "I mediated" in w.detail.toPlainText() and "1 match" in w.status.text()
    w.search.clear()
    qtbot.waitUntil(lambda: "URL shortener" in w.detail.toPlainText(), timeout=2000)


def test_window_delete_flow_respects_confirmation(qtbot, store):
    w, p = make_window(qtbot, filled(store), answer=False)
    w.delete_session()
    w.delete_all()
    w.delete_turn()
    assert store.counts() == (2, 3) and len(p.asked) == 3  # asked every time, deleted nothing
    p.answer = True
    w.delete_turn()  # newest session's only question -> session vanishes
    assert store.counts() == (1, 2) and w.sessions.count() == 1
    w.delete_session()
    assert store.counts() == (0, 0) and not w.del_all_btn.isEnabled()


def test_window_delete_all(qtbot, store):
    w, p = make_window(qtbot, filled(store))
    w.delete_all()
    assert "ALL history" in p.asked[0] and "2 sessions" in p.asked[0]
    assert store.counts() == (0, 0) and w.sessions.count() == 0 and w.detail.toPlainText() == ""


def test_window_export(qtbot, store, tmp_path):
    md, js = tmp_path / "out.md", tmp_path / "out.json"
    w, p = make_window(qtbot, filled(store), path=str(md))
    w.export_session()
    assert "## Q: Design a URL shortener" in md.read_text(encoding="utf-8")
    p.path = str(js)
    w.export_all()
    assert len(json.loads(js.read_text(encoding="utf-8"))) == 2
    p.path = None  # user cancelled the dialog
    w.export_all()
    p.path = str(tmp_path / "no" / "such" / "dir.md")
    w.export_all()
    assert "Export failed" in w.status.text()


def test_window_save_toggle_controls_store(qtbot, store):
    w, _ = make_window(qtbot, store)
    assert w.save_box.isChecked()
    w.save_box.setChecked(False)
    assert store.enabled is False and "OFF" in w.status.text()
    store.add_turn(turn())
    assert store.counts() == (0, 0)
    w.save_box.setChecked(True)
    assert store.enabled is True


def test_window_refresh_picks_up_new_turns(qtbot, store):
    w, _ = make_window(qtbot, store)
    assert w.sessions.count() == 0 and not w.del_all_btn.isEnabled()
    store.add_turn(turn("brand new"))
    w.refresh()
    assert w.sessions.count() == 1 and w.turns.count() == 1 and w.del_turn_btn.isEnabled()
    assert w.sessions.item(0).data(Qt.ItemDataRole.UserRole).title == "brand new"


# ---------------- history window: session dashboard (Step 18) ----------------
def test_window_shows_company_role_and_duration_for_linked_sessions(qtbot, store):
    iid = store.create_interview_session(company="Google", role_title="SWE")
    store.set_active_interview_session(iid)
    store.add_turn(turn("q1", "a1"))
    store.end_session()
    store.end_interview_session(iid)

    w, _ = make_window(qtbot, store)
    text = w.sessions.item(0).text()
    assert "Google — SWE" in text
    assert " · " in text and "m" in text  # a duration like "0m" once ended_at is set


def test_window_unlinked_sessions_still_show_their_plain_title(qtbot, store):
    w, _ = make_window(qtbot, filled(store))
    assert "URL shortener" in w.sessions.item(0).text()


def test_window_create_session_button_disabled_without_a_manager(qtbot, store):
    w, _ = make_window(qtbot, store)
    assert not w.create_session_btn.isEnabled()
    w.open_create_session()
    assert w._session_dialog is None  # a no-op: nothing to activate into


def test_window_create_session_opens_dialog_and_activates_on_created(qtbot, store):
    mgr = InterviewSessionManager(JevConfig(), store, base_profile=None)
    w, _ = make_window(qtbot, store, session_manager=mgr)
    assert w.create_session_btn.isEnabled()

    w.open_create_session()
    dialog = w._session_dialog
    assert dialog is not None
    dialog.company.setText("Google")
    dialog.next_btn.click()  # Details -> Preferences
    with qtbot.waitSignal(dialog.created):
        dialog.next_btn.click()  # Preferences -> create

    assert mgr.current is not None and mgr.current.company == "Google"
    assert "Google" in w.status.text()
    assert store.list_interview_sessions()[0].company == "Google"  # really persisted, not just set


def test_window_view_transcript_renders_full_session_inline(qtbot, store):
    w, _ = make_window(qtbot, filled(store))
    w.sessions.setCurrentRow(1)  # the older, 2-turn session
    assert w.view_transcript_btn.isEnabled()
    w.view_transcript()
    text = w.detail.toPlainText()
    assert "Tell me about a conflict" in text and "What is a mutex?" in text
    assert "I mediated." in text and "A lock." in text


def test_view_transcript_button_disabled_with_no_session_selected(qtbot, store):
    w, _ = make_window(qtbot, store)
    assert not w.view_transcript_btn.isEnabled()


def test_route_event_import_used():
    assert RouteEvent  # FakeJev yields RouteEvents; keeps the import honest


def test_turns_are_ordered_by_when_they_were_asked(store):
    """A cancelled answer saves after the newer one; history must still follow ask order."""
    clk = Clock()
    s = HistoryStore(":memory:", clock=clk)
    clk.t += 10
    s.add_turn(TurnRecord("second question", "answer", asked_at=clk.t - 5))  # saved first
    s.add_turn(TurnRecord("first question", "partial", status="interrupted", asked_at=clk.t - 8))
    questions = [t.question for t in s.get_turns(s.list_sessions()[0].id)]
    assert questions == ["first question", "second question"]
    assert TurnRecord("q").asked_at is None  # unspecified -> stamped at save time
    s.close()
