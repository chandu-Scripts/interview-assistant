from interview_assistant.app.interview_session import SessionDetails
from interview_assistant.history.store import HistoryStore
from interview_assistant.ui.start_screen import StartScreen, describe_session, details_of


def make(qtbot, store, activate=None, on_connect=None, on_dashboard=None):
    screen = StartScreen(
        store,
        None,
        activate
        or (lambda d: store.get_interview_session(store.create_interview_session(d.company))),
        on_connect or (lambda: None),
        on_dashboard or (lambda: None),
    )
    qtbot.addWidget(screen)
    return screen


def test_empty_history_invites_the_first_session(qtbot):
    screen = make(qtbot, HistoryStore(":memory:"))
    assert screen.sessions.count() == 0
    assert "No sessions yet" in screen.hint.text()
    assert not screen.start_btn.isEnabled()


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


def test_start_is_only_offered_for_a_session_not_yet_ended(qtbot):
    store = HistoryStore(":memory:")
    store.create_interview_session(company="Google")
    screen = make(qtbot, store)
    assert screen.start_btn.isEnabled()  # the first row is selected by default
    ended = store.create_interview_session(company="Accenture")
    store.end_interview_session(ended)
    screen.refresh()
    screen.sessions.setCurrentRow(0)  # newest first: Accenture (ended)
    assert not screen.start_btn.isEnabled()


def test_describe_session_names_an_untitled_session(qtbot):
    store = HistoryStore(":memory:")
    store.create_interview_session()
    (session,) = store.list_interview_sessions()
    assert describe_session(session).startswith("Untitled session")


def test_start_session_activates_the_saved_settings_and_shows_connect(qtbot):
    store = HistoryStore(":memory:")
    store.create_interview_session(company="Google", session_type="Regular", language="Hindi")
    seen = []
    screen = make(qtbot, store, activate=lambda d: (seen.append(d), _saved(store, d.company))[1])
    screen.start_btn.click()
    (details,) = seen
    assert (details.company, details.session_type, details.language) == (
        "Google",
        "Regular",
        "Hindi",
    )
    assert screen.pages.currentIndex() == 1
    assert "Google" in screen.connect_summary.text()


def test_connect_starts_the_session_and_hides_the_start_screen(qtbot):
    store = HistoryStore(":memory:")
    store.create_interview_session(company="Google")
    calls = []
    screen = make(qtbot, store, on_connect=lambda: calls.append("connect"))
    screen.show()
    screen.start_btn.click()
    screen.connect_btn.click()
    assert calls == ["connect"] and not screen.isVisible()


def test_back_returns_to_the_session_list(qtbot):
    store = HistoryStore(":memory:")
    store.create_interview_session(company="Google")
    screen = make(qtbot, store)
    screen.start_btn.click()
    assert screen.pages.currentIndex() == 1
    screen.back_btn.click()
    assert screen.pages.currentIndex() == 0


def test_dashboard_button_calls_its_action(qtbot):
    calls = []
    screen = make(qtbot, HistoryStore(":memory:"), on_dashboard=lambda: calls.append("dash"))
    screen.dashboard_btn.click()
    assert calls == ["dash"]


def test_details_of_carries_every_saved_choice(qtbot):
    store = HistoryStore(":memory:")
    sid = store.create_interview_session(
        company="Google",
        job_description="Azure",
        session_type="Regular",
        language="Hindi",
        auto_generate=True,
        save_transcript=False,
    )
    details = details_of(store.get_interview_session(sid), None)
    assert details == SessionDetails(
        company="Google",
        job_description="Azure",
        session_type="Regular",
        language="Hindi",
        auto_generate=True,
        save_transcript=False,
    )


def _saved(store, company):
    return store.get_interview_session(store.create_interview_session(company=company))


def test_create_session_opens_inside_the_same_window(qtbot):
    screen = make(qtbot, HistoryStore(":memory:"))
    screen.create_btn.click()
    assert screen.pages.currentWidget() is screen.create_page
    assert screen.pages.indexOf(screen.create_page) == 2 and screen.create_page.isWindow() is False


def test_cancelling_create_returns_to_the_list(qtbot):
    screen = make(qtbot, HistoryStore(":memory:"))
    screen.create_btn.click()
    screen.create_page.cancel_btn.click()
    assert screen.pages.currentIndex() == 0


def test_finishing_create_goes_on_to_connect(qtbot):
    store = HistoryStore(":memory:")
    screen = make(qtbot, store, activate=lambda d: _saved(store, d.company))
    screen.create_btn.click()
    screen.create_page.company.setText("Google")
    screen.create_page.next_btn.click()
    screen.create_page.next_btn.click()
    assert screen.pages.currentIndex() == 1
    assert "Google" in screen.connect_summary.text()
