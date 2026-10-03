from interview_assistant.history.store import HistoryStore
from interview_assistant.ui.start_screen import StartScreen, describe_session


def make(qtbot, store, on_create=lambda: None, on_dashboard=lambda: None):
    screen = StartScreen(store, on_create, on_dashboard)
    qtbot.addWidget(screen)
    return screen


def test_empty_history_invites_the_first_session(qtbot):
    screen = make(qtbot, HistoryStore(":memory:"))
    assert screen.sessions.count() == 0
    assert "No sessions yet" in screen.hint.text()


def test_previous_sessions_are_listed_with_their_status(qtbot):
    store = HistoryStore(":memory:")
    store.create_interview_session(company="Google", role_title="Azure data engineer")
    ended = store.create_interview_session(company="Accenture", role_title="Data engineer")
    store.end_interview_session(ended)
    screen = make(qtbot, store)
    texts = [screen.sessions.item(i).text() for i in range(screen.sessions.count())]
    assert len(texts) == 2
    assert any("Google — Azure data engineer" in t and "Ready to start" in t for t in texts)
    assert any("Accenture — Data engineer" in t and "Ended" in t for t in texts)


def test_describe_session_names_an_untitled_session(qtbot):
    store = HistoryStore(":memory:")
    store.create_interview_session()
    (session,) = store.list_interview_sessions()
    assert describe_session(session).startswith("Untitled session")


def test_create_and_dashboard_buttons_call_their_actions(qtbot):
    calls = []
    screen = make(
        qtbot,
        HistoryStore(":memory:"),
        on_create=lambda: calls.append("create"),
        on_dashboard=lambda: calls.append("dashboard"),
    )
    screen.create_btn.click()
    screen.dashboard_btn.click()
    assert calls == ["create", "dashboard"]


def test_showing_the_screen_picks_up_a_session_created_meanwhile(qtbot):
    store = HistoryStore(":memory:")
    screen = make(qtbot, store)
    assert screen.sessions.count() == 0
    store.create_interview_session(company="Google")
    screen.show()
    assert screen.sessions.count() == 1
    screen.hide()
