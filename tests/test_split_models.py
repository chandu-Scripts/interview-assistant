"""Session model roles, recommendations, persistence and migration."""

import sqlite3

import pytest
from PySide6.QtWidgets import QApplication

from interview_assistant.agents.jev import Jev, JevConfig
from interview_assistant.app.interview_session import InterviewSessionManager, SessionDetails
from interview_assistant.history.store import _SCHEMA, SCHEMA_VERSION, HistoryStore
from interview_assistant.llm.router import Route, RouteEvent, Router, RouterConfig
from interview_assistant.ui.session_dialog import SessionDialog
from interview_assistant.ui.start_screen import StartScreen, details_of
from interview_assistant.ui.theme import apply_theme
from tests.test_provider_picker import make, verify
from tests.test_router import Script


def wizard_with_catalog(qtbot, tmp_path, models=None):
    _, _, catalog, keys = make(qtbot, tmp_path, initial={"openai": "test-key"})
    verify(catalog, models=models)
    wizard = SessionDialog()
    qtbot.addWidget(wizard)
    wizard.company.setText("Example company")
    for selector in wizard.model_selectors:
        selector.bind(catalog)
    return wizard, catalog, keys


def test_recommendations_fill_both_and_reset_on_cancel(qtbot, tmp_path):
    wizard, _, _ = wizard_with_catalog(qtbot, tmp_path)
    wizard.recommend_models_btn.click()
    details = wizard.collect()
    assert details.coding_model_route == "openai:gpt-5.4"
    assert details.fast_model_route == "openai:gpt-5.4-mini"
    assert details.model_route_override == ""
    wizard.cancel_btn.click()
    assert all(selector.currentData() == "" for selector in wizard.model_selectors)
    assert wizard.model_notice.isHidden()


async def test_recommended_session_answers_with_both_selected_models(qtbot, tmp_path):
    wizard, _, _ = wizard_with_catalog(qtbot, tmp_path)
    wizard.recommend_models_btn.click()
    provider = Script("openai", ["Session answer"])
    router = Router(
        {"openai": provider},
        RouterConfig(
            chains={
                "fast": [Route("openai", "old-fast")],
                "smart": [Route("openai", "old-smart")],
            }
        ),
    )
    config = JevConfig(classify_with_llm=False)
    jev = Jev(router, config)
    history = HistoryStore(":memory:")
    try:
        manager = InterviewSessionManager(config, history, None)
        session = manager.activate(wizard.collect())
        assert session.fast_model_route == "openai:gpt-5.4-mini"
        for question in (
            "Write a Python function to reverse a linked list.",
            "Write a SQL query to find duplicate records.",
            "What is the difference between a list and a tuple?",
        ):
            events = [event async for event in jev.ask(question)]
            assert any(isinstance(event, RouteEvent) for event in events)
        assert provider.calls == ["gpt-5.4", "gpt-5.4", "gpt-5.4-mini"]
        manager.end_active()
    finally:
        history.close()


def test_unavailable_recommendation_does_not_partially_change_choices(qtbot, tmp_path):
    wizard, _, _ = wizard_with_catalog(qtbot, tmp_path, models=["gpt-5.4"])
    wizard.recommend_models_btn.click()
    assert all(selector.currentData() == "" for selector in wizard.model_selectors)
    assert "Both recommended" in wizard.model_notice.text()


def test_changed_key_requires_restart_for_recommendations(qtbot, tmp_path):
    wizard, _, _ = wizard_with_catalog(qtbot, tmp_path)
    for selector in wizard.model_selectors:
        selector.runtime_keys = {"openai": "old-fingerprint"}
    wizard.recommend_models_btn.click()
    assert "Restart" in wizard.model_notice.text()
    assert all(selector.currentData() == "" for selector in wizard.model_selectors)


def test_both_roles_allow_manual_provider_choice_and_validate_expiry(qtbot, tmp_path):
    wizard, catalog, keys = wizard_with_catalog(qtbot, tmp_path)
    keys.set("anthropic", "test-claude-key")
    verify(catalog, "anthropic", ["claude-sonnet-5"])
    wizard.recommend_models_btn.click()
    coding = wizard.coding_model_override
    coding.showPopup()
    assert coding.picker.windowTitle() == "Coding model"
    coding.picker.choose("anthropic:claude-sonnet-5")
    assert wizard.collect().coding_model_route == "anthropic:claude-sonnet-5"
    assert wizard.collect().fast_model_route == "openai:gpt-5.4-mini"
    # Expiry between selection and collection must reject the session, not silently use Auto.
    catalog.clock = lambda: 10**12
    with pytest.raises(ValueError, match="Coding model"):
        wizard.collect()


@pytest.mark.parametrize(
    "question,model",
    [
        ("Write a Python function to reverse a linked list.", "coding"),
        ("Write a SQL query to find the second highest salary.", "coding"),
        ("What is the difference between a list and a tuple?", "fast"),
        ("Tell me about yourself.", "fast"),
        ("Tell me about a time you resolved a conflict on your team.", "fast"),
        ("Design a scalable chat system.", "fast"),
    ],
)
@pytest.mark.parametrize("auto", [False, True])
async def test_question_type_uses_session_role(question, model, auto):
    provider = Script("openai", ["Answer"])
    router = Router(
        {"openai": provider},
        RouterConfig(
            chains={
                "fast": [Route("openai", "auto-fast")],
                "smart": [Route("openai", "auto-coding")],
            }
        ),
    )
    jev = Jev(
        router,
        JevConfig(
            classify_with_llm=False,
            coding_model_route="" if auto else "openai:coding",
            fast_model_route="" if auto else "openai:fast",
        ),
    )
    events = [event async for event in jev.ask(question)]
    assert any(isinstance(event, RouteEvent) for event in events)
    assert provider.calls == [("auto-" if auto else "") + model]


@pytest.mark.parametrize("save_transcript", [False, True])
def test_models_survive_session_storage_resume_and_end(tmp_path, save_transcript):
    path = tmp_path / "history.db"
    history = HistoryStore(path)
    details = SessionDetails(
        company="Test",
        coding_model_route="openai:gpt-5.4",
        fast_model_route="openai:gpt-5.4-mini",
        save_transcript=save_transcript,
    )
    sid = history.create_interview_session(
        company=details.company,
        coding_model_route=details.coding_model_route,
        fast_model_route=details.fast_model_route,
        save_transcript=save_transcript,
    )
    if save_transcript:
        history.close()
        history = HistoryStore(path)
    try:
        config = JevConfig()
        manager = InterviewSessionManager(config, history, None)
        session = manager.resume(sid)
        assert config.coding_model_route == details.coding_model_route
        assert config.fast_model_route == details.fast_model_route
        assert details_of(session, None).fast_model_route == details.fast_model_route
        with pytest.raises(ValueError):
            manager.activate(SessionDetails(coding_model_route="bad-route"))
        assert manager.current is session
        manager.end_active()
        assert config.coding_model_route is None and config.fast_model_route is None
    finally:
        history.close()


def test_version5_migration_preserves_legacy_sessions_and_notes(tmp_path):
    path = tmp_path / "old.db"
    schema5 = (
        _SCHEMA.replace("    coding_model_route TEXT,\n", "")
        .replace("    fast_model_route TEXT,\n", "")
        .replace("    use_builtin_prompt INTEGER NOT NULL DEFAULT 1,\n", "")
    )
    with sqlite3.connect(path) as old:
        old.executescript(schema5)
        old.execute("PRAGMA user_version = 5")
        old.execute(
            "INSERT INTO interview_sessions(id, created_at, company, model_route_override) "
            "VALUES (1, 1, 'Previous company', 'openai:legacy')"
        )
        old.execute("INSERT INTO interview_notes VALUES (1, 'Keep these notes')")
    history = HistoryStore(path)
    try:
        previous = history.get_interview_session(1)
        assert previous.model_route_override == "openai:legacy"
        assert previous.coding_model_route is None and previous.fast_model_route is None
        assert history.get_interview_notes(1) == "Keep these notes"
        new_id = history.create_interview_session(coding_model_route="", fast_model_route="")
        assert history.get_interview_session(new_id).fast_model_route == ""
    finally:
        history.close()
    with sqlite3.connect(path) as check:
        assert check.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


def test_split_preferences_fit_workspace(qtbot, qapp, tmp_path):
    apply_theme(qapp)
    history = HistoryStore(":memory:")
    try:
        screen = StartScreen(history, None, lambda d: None, lambda: None, lambda: None)
        qtbot.addWidget(screen)
        screen.resize(960, 680)
        screen.show()
        screen.create_btn.click()
        wizard = screen.create_page
        wizard.company.setText("Example company")
        wizard.next_btn.click()
        QApplication.processEvents()
        assert screen.width() <= 960 and screen.height() <= 680
        assert wizard.coding_model_override.isVisible()
        assert wizard.fast_model_override.isVisible()
        assert wizard.recommend_models_btn.isVisible()
        screen.grab().save(str(tmp_path / "split-models.png"))
    finally:
        history.close()
