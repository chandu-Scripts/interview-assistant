"""Built-in answer style is an explicit, persisted per-session choice."""

import sqlite3

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QApplication, QLabel, QScrollArea

from interview_assistant.agents.jev import Jev, JevConfig
from interview_assistant.agents.specialists import (
    BASE,
    BUILTIN_FEATURE_TITLES,
    SPECIALISTS,
    system_prompt,
)
from interview_assistant.app.interview_session import InterviewSessionManager, SessionDetails
from interview_assistant.config.settings import Settings
from interview_assistant.history.store import _SCHEMA, SCHEMA_VERSION, HistoryStore
from interview_assistant.llm.router_factory import build_router
from interview_assistant.ui.controls import ToggleSwitch
from interview_assistant.ui.session_dialog import SessionDialog
from interview_assistant.ui.start_screen import StartScreen, details_of
from interview_assistant.ui.theme import apply_theme
from tests.test_router import Script


@pytest.mark.parametrize("spec", SPECIALISTS.values())
def test_disabled_prompt_omits_style_but_keeps_custom_guidance(spec):
    prompt = system_prompt(
        spec,
        "Project: ADF",
        instructions="Use a comparison table, without fillers.",
        answer_format="Bullet points",
        answer_length="Detailed",
        answer_tone="Formal",
        language="Telugu",
        use_builtin_prompt=False,
    )
    assert "Use a comparison table, without fillers." in prompt
    assert "Project: ADF" in prompt and "length: Detailed" in prompt
    assert "tone: Formal" in prompt and "Answer in Telugu" in prompt
    for marker in ("FORMAT LOCK", "Check 0", "FINAL DELIVERY RULE", "Line N", spec.prompt):
        assert marker not in prompt
    assert system_prompt(spec, None).startswith(BASE)


def test_toggle_collects_both_states_and_resets(qtbot):
    wizard = SessionDialog()
    qtbot.addWidget(wizard)
    wizard.company.setText("Example")
    assert wizard.collect().use_builtin_prompt is True
    wizard.ai_instructions.setPlainText("Use my style.")
    wizard.builtin_prompt_btn.click()
    details = wizard.collect()
    assert details.use_builtin_prompt is False
    assert details.ai_instructions == "Use my style."
    assert "Off" in wizard.builtin_prompt_btn.text()
    wizard.cancel_btn.click()
    assert wizard.builtin_prompt_btn.isChecked()


def test_modes_disable_preferences_without_discarding_selections(qtbot):
    wizard = SessionDialog()
    qtbot.addWidget(wizard)
    wizard.company.setText("Example")
    wizard.next_btn.click()
    wizard.show()
    combos = (wizard.answer_format, wizard.answer_length, wizard.answer_tone)
    assert all(not combo.isEnabled() for combo in combos)
    assert wizard.preference_notice.isVisible()
    assert all("Turn off Built-in prompt" in combo.toolTip() for combo in combos)
    assert wizard.ai_instructions.isEnabled() and wizard.language.isEnabled()
    wizard.builtin_prompt_btn.click()
    assert all(combo.isEnabled() for combo in combos)
    assert wizard.preference_notice.isHidden()
    for combo, value in zip(combos, ("Bullet points", "Detailed", "Formal"), strict=True):
        combo.setCurrentIndex(combo.findData(value))
    wizard.builtin_prompt_btn.click()
    assert all(not combo.isEnabled() for combo in combos)
    before = [combo.currentIndex() for combo in combos]
    for combo in combos:
        qtbot.mouseClick(combo, Qt.MouseButton.LeftButton)
        qtbot.keyClick(combo, Qt.Key.Key_Down)
        assert not combo.view().isVisible()
    assert [combo.currentIndex() for combo in combos] == before
    details = wizard.collect()
    assert (details.answer_format, details.answer_length, details.answer_tone) == (
        "Bullet points",
        "Detailed",
        "Formal",
    )
    wizard.builtin_prompt_btn.click()
    assert [combo.currentIndex() for combo in combos] == before
    wizard.cancel_btn.click()
    assert all(not combo.isEnabled() and combo.currentIndex() == 0 for combo in combos)


@pytest.mark.parametrize("spec", SPECIALISTS.values())
def test_builtin_mode_ignores_dropdowns_but_keeps_extra_instructions(spec):
    prompt = system_prompt(
        spec,
        "Project A: ADF",
        answer_format="Bullet points",
        answer_length="Detailed",
        answer_tone="Formal",
        instructions="Explain each assumption.",
        language="Hindi",
    )
    assert "Answer preferences for this session" not in prompt
    assert "format: Bullet points" not in prompt
    assert "length: Detailed" not in prompt and "tone: Formal" not in prompt
    assert "Explain each assumption." in prompt and "Answer in Hindi" in prompt
    assert "Project A: ADF" in prompt


def test_features_show_titles_in_same_window_and_reset(qtbot):
    wizard = SessionDialog()
    qtbot.addWidget(wizard)
    wizard.company.setText("Example")
    wizard.next_btn.click()
    wizard.show()
    assert wizard.prompt_features_panel.isHidden()
    qtbot.mouseClick(wizard.prompt_features_btn, Qt.MouseButton.LeftButton)
    assert wizard.prompt_features_panel.isVisible()
    assert wizard.prompt_features_panel.window() is wizard.window()
    labels = [label.text() for label in wizard.prompt_features_panel.findChildren(QLabel)]
    assert labels == ["Built-in prompt features", *BUILTIN_FEATURE_TITLES]
    assert all("FORMAT LOCK" not in label for label in labels)
    wizard.builtin_prompt_btn.click()
    assert wizard.prompt_features_panel.isVisible()
    assert wizard.prompt_features_btn.isEnabled()
    qtbot.mouseClick(wizard.prompt_features_btn, Qt.MouseButton.LeftButton)
    assert wizard.prompt_features_panel.isHidden()
    wizard.prompt_features_btn.setFocus()
    qtbot.keyClick(wizard.prompt_features_btn, Qt.Key.Key_Space)
    assert wizard.prompt_features_panel.isVisible()
    wizard.prompt_features_close.click()
    assert wizard.prompt_features_panel.isHidden()
    wizard.prompt_features_btn.click()
    wizard.cancel_btn.click()
    assert wizard.prompt_features_panel.isHidden()
    assert not wizard.prompt_features_btn.isChecked()


def test_sliding_switch_click_keyboard_and_animation(qtbot):
    switch = ToggleSwitch()
    qtbot.addWidget(switch)
    switch.setText("Built-in prompt")
    switch.setChecked(True)
    switch.show()
    assert switch._position == 1.0
    qtbot.mouseClick(switch, Qt.MouseButton.LeftButton, pos=QPoint(25, 18))
    assert not switch.isChecked()
    qtbot.waitUntil(lambda: switch._position == 0.0)
    switch.setFocus()
    qtbot.keyClick(switch, Qt.Key.Key_Space)
    assert switch.isChecked()
    qtbot.waitUntil(lambda: switch._position == 1.0)
    switch.setEnabled(False)
    qtbot.mouseClick(switch, Qt.MouseButton.LeftButton, pos=QPoint(25, 18))
    assert switch.isChecked()


@pytest.mark.parametrize("save", [False, True])
def test_prompt_choice_persists_resumes_and_resets(tmp_path, save):
    path = tmp_path / "history.db"
    history = HistoryStore(path)
    sid = history.create_interview_session(
        company="Test",
        use_builtin_prompt=False,
        save_transcript=save,
        ai_instructions="My instructions",
    )
    if save:
        history.close()
        history = HistoryStore(path)
    try:
        config = JevConfig()
        manager = InterviewSessionManager(config, history, None)
        session = manager.resume(sid)
        assert session.use_builtin_prompt is False
        assert config.use_builtin_prompt is False
        assert details_of(session, None).use_builtin_prompt is False
        manager.end_active()
        assert config.use_builtin_prompt is True
        assert manager.activate(SessionDetails()).use_builtin_prompt is True
    finally:
        history.close()


@pytest.mark.parametrize("enabled", [False, True])
async def test_toggle_reaches_actual_generation_request(enabled):
    requests = []

    class Capture(Script):
        async def stream(self, request):
            requests.append(request)
            async for event in super().stream(request):
                yield event

    provider = Capture("openai", ["Answer"])
    config = JevConfig(classify_with_llm=False)
    jev = Jev(build_router(Settings(_env_file=None), {"openai": provider}), config)
    history = HistoryStore(":memory:")
    try:
        manager = InterviewSessionManager(config, history, None)
        manager.activate(
            SessionDetails(
                use_builtin_prompt=enabled,
                ai_instructions="Use my style.",
                answer_format="Bullet points",
                answer_length="Detailed",
                answer_tone="Formal",
            )
        )
        # Resume the persisted session, including dormant selections made before enabling it.
        saved_id = manager.current.id
        config = JevConfig(classify_with_llm=False)
        manager = InterviewSessionManager(config, history, None)
        manager.resume(saved_id)
        jev.config = config
        _ = [event async for event in jev.ask("Write a SQL query to find duplicate records.")]
        assert len(requests) == 1
        assert ("FORMAT LOCK" in requests[0].system) is enabled
        assert "Use my style." in requests[0].system
        assert ("Answer preferences for this session" in requests[0].system) is not enabled
        for preference in ("format: Bullet points", "length: Detailed", "tone: Formal"):
            assert (preference in requests[0].system) is not enabled
    finally:
        history.close()


def test_schema6_upgrade_defaults_previous_sessions_to_enabled(tmp_path):
    path = tmp_path / "old.db"
    old_schema = _SCHEMA.replace("    use_builtin_prompt INTEGER NOT NULL DEFAULT 1,\n", "")
    with sqlite3.connect(path) as old:
        old.executescript(old_schema)
        old.execute("PRAGMA user_version = 6")
        old.execute(
            "INSERT INTO interview_sessions(id, created_at, ai_instructions) "
            "VALUES (1, 1, 'Keep my instructions')"
        )
    history = HistoryStore(path)
    try:
        session = history.get_interview_session(1)
        assert session.use_builtin_prompt is True
        assert session.ai_instructions == "Keep my instructions"
    finally:
        history.close()
    with sqlite3.connect(path) as check:
        assert check.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


def test_toggle_below_instructions_fits_glass_workspace(qtbot, qapp, tmp_path):
    apply_theme(qapp)
    history = HistoryStore(":memory:")
    try:
        screen = StartScreen(history, None, lambda d: None, lambda: None, lambda: None)
        qtbot.addWidget(screen)
        screen.resize(960, 680)
        screen.show()
        screen.create_btn.click()
        wizard = screen.create_page
        wizard.company.setText("Example")
        wizard.next_btn.click()
        QApplication.processEvents()
        parent = wizard.builtin_prompt_btn.parentWidget()
        while parent is not None and not isinstance(parent, QScrollArea):
            parent = parent.parentWidget()
        assert parent is not None
        parent.ensureWidgetVisible(wizard.builtin_prompt_btn)
        assert wizard.builtin_prompt_btn.y() > wizard.ai_instructions.y()
        assert screen.height() <= 680
        screen.grab().save(str(tmp_path / "builtin-prompt.png"))
        wizard.builtin_prompt_btn.click()
        qtbot.waitUntil(lambda: wizard.builtin_prompt_btn._position == 0.0)
        screen.grab().save(str(tmp_path / "builtin-prompt-off.png"))
        wizard.prompt_features_btn.click()
        # Let nested layouts and the native paint pass settle before geometry checks.
        qtbot.wait(100)
        qtbot.waitUntil(
            lambda: all(
                parent.viewport()
                .rect()
                .contains(wizard.prompt_features_panel.mapTo(parent.viewport(), point))
                for point in (
                    wizard.prompt_features_panel.rect().topLeft(),
                    wizard.prompt_features_panel.rect().bottomRight(),
                )
            )
        )
        assert screen.height() <= 680 and screen.width() <= 960
        assert wizard.prompt_features_panel.width() <= parent.viewport().width()
        assert wizard.prompt_features_panel.window() is screen
        screen.grab().save(str(tmp_path / "builtin-prompt-features.png"))
    finally:
        history.close()
