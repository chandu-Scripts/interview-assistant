import sqlite3

import pytest

from interview_assistant.app.async_runner import AsyncRunner
from interview_assistant.app.post_call import CONTEXT_LIMIT, PostCallController
from interview_assistant.cost.tracker import Budget, CostTracker
from interview_assistant.history.store import SCHEMA_VERSION, HistoryStore, TurnRecord
from interview_assistant.llm.router import Route, Router, RouterConfig
from interview_assistant.llm.types import ErrorKind, LLMError
from interview_assistant.ui.post_call_page import PostCallPage
from tests.test_router import Script


class Spy(Script):
    def __init__(self, name, steps):
        super().__init__(name, steps)
        self.requests = []

    async def stream(self, request):
        self.requests.append(request)
        async for event in super().stream(request):
            yield event


@pytest.fixture
def review_stack():
    runner = AsyncRunner()
    tracker = CostTracker()
    primary = Spy("a", ["Review complete"])
    fallback = Spy("b", ["Fallback review"])
    router = Router(
        {"a": primary, "b": fallback},
        RouterConfig(chains={"smart": [Route("a", "m"), Route("b", "m")]}),
        tracker.record_attempt,
    )
    controller = PostCallController(router, runner, tracker)
    yield controller, primary, fallback, tracker
    controller.stop()
    runner.stop()


def seed(store, company="Northwind"):
    sid = store.create_interview_session(company=company)
    store.set_active_interview_session(sid)
    store.add_turn(TurnRecord("First question", "First answer"))
    store.end_session()
    store.add_turn(TurnRecord("Second question", "Second answer"))
    store.end_interview_session(sid)
    return sid


def test_v4_upgrade_preserves_turns_and_notes_persist_and_delete(tmp_path):
    path = tmp_path / "history.db"
    store = HistoryStore(path)
    sid = seed(store)
    store.close()
    with sqlite3.connect(path) as db:
        db.execute("DROP TABLE interview_notes")
        db.execute("PRAGMA user_version = 4")
    store = HistoryStore(path)
    assert len(store.get_interview_turns(sid)) == 2
    assert store._db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    store.save_interview_notes(sid, "Practice examples")
    store.close()
    store = HistoryStore(path)
    assert store.get_interview_notes(sid) == "Practice examples"
    store.suspended = True
    with pytest.raises(ValueError, match="incognito"):
        store.save_interview_notes(sid, "must not save")
    assert store.get_interview_notes(sid) == "Practice examples"
    store.delete_all()
    assert store.get_interview_notes(sid) == ""
    store.close()


def test_notes_drafts_and_full_selected_transcript(qtbot):
    store = HistoryStore(":memory:")
    first = seed(store)
    second = seed(store, "Other")
    page = PostCallPage(store)
    qtbot.addWidget(page)
    page.load(first)
    assert "First answer" in page.transcript.toPlainText()
    assert "Second answer" in page.transcript.toPlainText()
    page.notes.setPlainText("Unsaved draft")
    page.load(second)
    assert page.notes.toPlainText() == ""
    page.load(first)
    assert page.notes.toPlainText() == "Unsaved draft"
    page.save_btn.click()
    assert store.get_interview_notes(first) == "Unsaved draft"
    page.notes.clear()
    page.save_btn.click()
    assert store.get_interview_notes(first) == ""
    empty = store.create_interview_session(company="Empty")
    page.load(empty)
    assert not page.ask_btn.isEnabled()
    assert "No Q&A" in page.transcript.toPlainText()
    store.close()


def test_interview_lookup_has_no_dashboard_limit_and_retention_clears_notes():
    now = [0.0]
    store = HistoryStore(":memory:", clock=lambda: now[0])
    first = seed(store)
    store.save_interview_notes(first, "Old notes")
    for _ in range(205):
        other = store.create_interview_session(company="Other")
        store.set_active_interview_session(other)
        store.add_turn(TurnRecord("Unrelated question", "Private unrelated answer"))
    turns = store.get_interview_turns(first)
    assert [t.question for t in turns] == ["First question", "Second question"]
    store.end_session()
    now[0] = 40 * 86400
    store.purge_older_than(7)
    assert store.get_interview_notes(first) == ""
    store.close()


def test_ask_redacts_bounds_context_and_records_cost(qtbot, review_stack):
    controller, provider, _, tracker = review_stack
    done = []
    controller.finished.connect(lambda gen, text: done.append(text))
    controller.ask("Review person@example.com", "Question 1: user@example.com " + "x" * 30000)
    qtbot.waitUntil(lambda: bool(done))
    request = provider.requests[0]
    text = request.messages[0].content
    assert "person@example.com" not in text and "user@example.com" not in text
    assert len(text) < CONTEXT_LIMIT + 300
    assert tracker.summary("session").calls == 1


def test_fallback_replaces_partial_review(qtbot, review_stack):
    controller, first, second, _ = review_stack
    first.steps = ["discard this", LLMError("a", ErrorKind.SERVER, "test")]
    shown, done = [], []
    controller.updated.connect(lambda gen, text: shown.append(text))
    controller.finished.connect(lambda gen, text: done.append(text))
    controller.ask("Review", "Saved Q&A")
    qtbot.waitUntil(lambda: bool(done))
    assert second.calls == ["m"]
    assert shown[-1] == "Fallback review" and "" in shown


def test_budget_and_presentation_block_requests(qtbot, review_stack):
    controller, provider, _, tracker = review_stack
    done = []
    controller.finished.connect(lambda gen, text: done.append(text))
    tracker.budget = Budget(0, hard_stop=True)
    controller.ask("Review", "Saved Q&A")
    qtbot.waitUntil(lambda: bool(done))
    assert "Budget" in done[-1] and not provider.calls
    tracker.budget = None
    controller.set_paused(True)
    controller.ask("Review", "Saved Q&A")
    qtbot.waitUntil(lambda: len(done) == 2)
    assert "paused" in done[-1] and not provider.calls


def test_stopping_and_switching_sessions_ignore_late_output(qtbot, review_stack):
    controller, provider, _, _ = review_stack
    provider.steps = [("sleep", 0.5), "late answer"]
    store = HistoryStore(":memory:")
    first, second = seed(store), seed(store, "Second")
    page = PostCallPage(store, controller)
    qtbot.addWidget(page)
    page.load(first)
    page.question.setText("Review")
    page.ask_btn.click()
    qtbot.waitUntil(lambda: bool(provider.calls))
    old_gen = page._generation
    page.load(second)
    qtbot.waitUntil(lambda: provider.closed == 1)
    page._updated(old_gen, "late answer")
    assert page.answer.toPlainText() == ""
    store.delete_all()
    page.question.setText("Review deleted data")
    page.ask_btn.click()
    assert len(provider.calls) == 1
    assert "No saved Q&A" in page.ask_status.text()
    store.close()
