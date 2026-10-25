from interview_assistant.agents.jev import JevConfig
from interview_assistant.agents.specialists import SPECIALISTS, system_prompt
from interview_assistant.agents.types import Category
from interview_assistant.app.interview_session import InterviewSessionManager, SessionDetails
from interview_assistant.app.resume_library import ResumeLibrary
from interview_assistant.history.store import HistoryStore
from interview_assistant.ui import session_dialog
from interview_assistant.ui.session_dialog import ATTACH_NEW, SessionDialog


def dialog(qtbot, library=None):
    d = SessionDialog(library)
    qtbot.addWidget(d)
    d.company.setText("Google")
    d.next_btn.click()
    return d


def test_session_type_defaults_to_interview_and_regular_is_collected(qtbot):
    d = dialog(qtbot)
    assert d.collect().session_type == "Interview"
    d.type_regular.click()
    assert d.collect().session_type == "Regular"


def test_preferences_defaults_are_english_auto_off_save_on(qtbot):
    d = dialog(qtbot)
    details = d.collect()
    assert (details.language, details.auto_generate, details.save_transcript) == (
        "English",
        False,
        True,
    )
    d.language.setCurrentText("Hindi")
    d.auto_generate.setChecked(True)
    d.save_transcript.setChecked(False)
    details = d.collect()
    assert (details.language, details.auto_generate, details.save_transcript) == (
        "Hindi",
        True,
        False,
    )


def test_resume_dropdown_offers_saved_resumes_and_no_resume(qtbot, tmp_path):
    lib = ResumeLibrary(tmp_path / "resumes.json")
    lib.add("cv_azure.pdf", "Azure data engineer, 6 years")
    d = dialog(qtbot, lib)
    names = [d.resume_combo.itemData(i) for i in range(d.resume_combo.count())]
    assert names == ["", "cv_azure.pdf", ATTACH_NEW]
    assert d.collect().resume_override == ""  # nothing chosen: the profile is used
    d.resume_combo.setCurrentIndex(names.index("cv_azure.pdf"))
    d.resume_combo.activated.emit(names.index("cv_azure.pdf"))
    details = d.collect()
    assert details.resume_name == "cv_azure.pdf"
    assert details.resume_override == "Azure data engineer, 6 years"


def test_attach_new_resume_saves_it_and_selects_it(qtbot, tmp_path, monkeypatch):
    cv = tmp_path / "cv_genai.txt"
    cv.write_text("GenAI engineer", encoding="utf-8")
    monkeypatch.setattr(
        session_dialog.QFileDialog, "getOpenFileName", lambda *a, **k: (str(cv), "")
    )
    lib = ResumeLibrary(tmp_path / "resumes.json")
    d = dialog(qtbot, lib)
    index = d.resume_combo.findData(ATTACH_NEW)
    d.resume_combo.setCurrentIndex(index)
    d.resume_combo.activated.emit(index)
    assert [r.name for r in lib.list()] == ["cv_genai.txt"]
    assert d.collect().resume_name == "cv_genai.txt"
    assert "Saved cv_genai.txt" in d.resume_status.text()


def test_cancelled_resume_attach_keeps_the_previous_choice(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(session_dialog.QFileDialog, "getOpenFileName", lambda *a, **k: ("", ""))
    d = dialog(qtbot, ResumeLibrary(tmp_path / "resumes.json"))
    index = d.resume_combo.findData(ATTACH_NEW)
    d.resume_combo.setCurrentIndex(index)
    d.resume_combo.activated.emit(index)
    assert d.collect().resume_name == ""


def test_session_settings_are_stored_and_drive_the_answers(tmp_path):
    store = HistoryStore(":memory:")
    config = JevConfig()
    manager = InterviewSessionManager(config, store, base_profile=None)
    session = manager.activate(
        SessionDetails(
            company="Accenture",
            session_type="Regular",
            language="Hindi",
            auto_generate=True,
            save_transcript=False,
        )
    )
    assert (session.session_type, session.language) == ("Regular", "Hindi")
    assert (session.auto_generate, session.save_transcript) == (True, False)
    assert config.language == "Hindi"
    manager.end_active()
    assert config.language == "English"


def test_language_other_than_english_is_put_in_the_prompt():
    spec = SPECIALISTS[Category.CONCEPT]
    assert "Answer in Hindi." in system_prompt(spec, None, language="Hindi")
    assert "Answer in" not in system_prompt(spec, None, language="English")
    assert "Answer in" not in system_prompt(spec, None)


def test_fixed_fields_cannot_be_typed_into(qtbot):
    d = dialog(qtbot)
    for combo in (d.answer_format, d.answer_tone, d.language, d.model_override):
        assert not combo.isEditable()


def test_completed_wizard_starts_a_clean_draft_and_back_preserves_input(qtbot):
    d = dialog(qtbot)
    d.ai_instructions.setPlainText("Use examples")
    d.back_btn.click()
    assert d.company.text() == "Google"
    d.next_btn.click()
    assert d.ai_instructions.toPlainText() == "Use examples"
    with qtbot.waitSignal(d.created) as signal:
        d.next_btn.click()
    assert signal.args[0].company == "Google"
    assert d.company.text() == "" and d.ai_instructions.toPlainText() == ""
    assert d.pages.currentIndex() == 0


def test_model_choices_are_only_auto_or_the_configured_models(qtbot):
    d = SessionDialog(None, ["openai:gpt-4o-mini", "anthropic:claude-sonnet-5"])
    qtbot.addWidget(d)
    items = [d.model_override.itemData(i) for i in range(d.model_override.count())]
    assert items == ["", "openai:gpt-4o-mini", "anthropic:claude-sonnet-5"]


def test_session_instructions_respect_builtin_mode_and_preserve_factuality():
    from interview_assistant.agents.specialists import SPECIALISTS, system_prompt

    prompt = system_prompt(
        SPECIALISTS[Category.CONCEPT], None, instructions="Speak in short spoken sentences."
    )
    assert "Speak in short spoken sentences." in prompt
    assert "preferences within the format lock" in prompt
    assert "never override the rules against inventing facts" in prompt
    custom_prompt = system_prompt(
        SPECIALISTS[Category.CONCEPT],
        None,
        instructions="Speak in short spoken sentences.",
        use_builtin_prompt=False,
    )
    assert "Speak in short spoken sentences." in custom_prompt
    assert "FORMAT LOCK" not in custom_prompt
    assert "Do not invent personal experience" in custom_prompt
