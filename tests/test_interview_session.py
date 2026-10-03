"""InterviewSessionManager: activating a "Create Interview Session" against the live Jev config
and history store. No Qt here - this is pure glue logic, deliberately kept independent of the
dialog that collects the details (see ui/session_dialog.py)."""

import pytest

from interview_assistant.agents.jev import JevConfig
from interview_assistant.app.interview_session import InterviewSessionManager, SessionDetails
from interview_assistant.history.store import HistoryStore


@pytest.fixture
def history():
    h = HistoryStore(":memory:")
    yield h
    h.close()


@pytest.fixture
def config():
    return JevConfig(profile="Global resume summary")


def details(**kw) -> SessionDetails:
    base = dict(company="Google", role_title="Software Engineer", job_description="Build search.")
    base.update(kw)
    return SessionDetails(**base)


def test_activate_mutates_jev_config_and_stores_the_session(history, config):
    mgr = InterviewSessionManager(config, history, base_profile="Global resume summary")
    session = mgr.activate(details(ai_instructions="Be concise."))

    assert mgr.current is session
    assert session.company == "Google" and session.job_description == "Build search."
    assert config.company == "Google" and config.job_description == "Build search."
    assert config.instructions == "Be concise."
    # No resume_override given -> falls back to the original global profile, unchanged.
    assert config.profile == "Global resume summary"

    stored = history.get_interview_session(session.id)
    assert stored is not None and stored.company == "Google"


def test_resume_override_replaces_the_global_profile(history, config):
    mgr = InterviewSessionManager(config, history, base_profile="Global resume summary")
    mgr.activate(details(resume_override="Session-specific resume"))
    assert config.profile == "Session-specific resume"


def test_activating_a_new_session_ends_the_previous_one(history, config):
    mgr = InterviewSessionManager(config, history, base_profile=None)
    first = mgr.activate(details(company="Google"))
    second = mgr.activate(details(company="Meta"))

    assert mgr.current is second
    ended_first = history.get_interview_session(first.id)
    assert ended_first is not None and ended_first.ended_at is not None
    still_open_second = history.get_interview_session(second.id)
    assert still_open_second is not None and still_open_second.ended_at is None


def test_end_active_reverts_config_to_the_base_profile(history, config):
    mgr = InterviewSessionManager(config, history, base_profile="Global resume summary")
    session = mgr.activate(details(resume_override="Override", ai_instructions="note"))
    mgr.end_active()

    assert mgr.current is None
    assert config.profile == "Global resume summary"
    assert config.company == "" and config.job_description == "" and config.instructions == ""
    ended = history.get_interview_session(session.id)
    assert ended is not None and ended.ended_at is not None


def test_end_active_with_nothing_active_is_a_noop(history, config):
    mgr = InterviewSessionManager(config, history, base_profile="Global resume summary")
    mgr.end_active()  # must not raise
    assert mgr.current is None and config.profile == "Global resume summary"


def test_new_turns_are_tagged_with_the_active_interview_session(history, config):
    from interview_assistant.history.store import TurnRecord

    mgr = InterviewSessionManager(config, history, base_profile=None)
    session = mgr.activate(details())
    history.add_turn(TurnRecord(question="q1", answer="a1"))
    history.end_session()

    (turn_session,) = history.list_sessions()
    row = history._db.execute(
        "SELECT interview_session_id FROM sessions WHERE id = ?", (turn_session.id,)
    ).fetchone()
    assert row[0] == session.id
