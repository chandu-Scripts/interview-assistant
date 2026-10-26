import asyncio
import threading

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from interview_assistant.agents.jev import Jev, JevConfig
from interview_assistant.app.interview_session import InterviewSessionManager
from interview_assistant.app.resume_library import ResumeLibrary
from interview_assistant.config.settings import Settings
from interview_assistant.history.store import HistoryStore
from interview_assistant.llm.router_factory import build_router
from interview_assistant.llm.types import StreamEvent
from interview_assistant.ui.session_dialog import SessionDialog
from interview_assistant.ui.start_screen import StartScreen
from interview_assistant.ui.theme import apply_theme


def attach(qtbot, monkeypatch, wizard, path):
    monkeypatch.setattr(
        "interview_assistant.ui.session_dialog.QFileDialog.getOpenFileName",
        lambda *a, **k: (str(path), ""),
    )
    wizard.attach_new_resume()
    qtbot.waitUntil(lambda: wizard._resume_worker is None)


@pytest.mark.parametrize("incognito", [True, False])
def test_table_resume_reaches_answer_request_instead_of_old_profile(
    qtbot, tmp_path, monkeypatch, incognito
):
    import docx

    document = docx.Document()
    table = document.add_table(rows=1, cols=1)
    table.cell(0, 0).text = "Project Cedar: I built 12 ADF pipelines. Team size: six."
    path = tmp_path / "new.docx"
    document.save(path)
    library = ResumeLibrary(tmp_path / "resumes.json", can_write=lambda: not incognito)
    wizard = SessionDialog(library)
    qtbot.addWidget(wizard)
    wizard.company.setText("Example company")
    attach(qtbot, monkeypatch, wizard, path)
    assert wizard.next_btn.isEnabled()
    if incognito:
        assert "session only" in wizard.resume_status.text()
        assert not (tmp_path / "resumes.json").exists()
    else:
        assert library.list()[0].text == table.cell(0, 0).text
    requests = []

    class Provider:
        name = "openai"

        async def stream(self, request):
            requests.append(request)
            yield StreamEvent(delta="12 pipelines.", finish_reason="stop")

    config = JevConfig(classify_with_llm=False, profile="Old unrelated profile")
    history = HistoryStore(tmp_path / "history.db")
    history.suspended = incognito
    manager = InterviewSessionManager(config, history, "Old unrelated profile")
    try:
        manager.activate(wizard.collect())
        jev = Jev(build_router(Settings(_env_file=None), {"openai": Provider()}), config)

        async def ask():
            return [event async for event in jev.ask("How many pipelines did you build?")]

        asyncio.run(ask())
        assert len(requests) == 1
        assert table.cell(0, 0).text in requests[0].system
        assert "Old unrelated profile" not in requests[0].system
        assert requests[0].messages[-1].content == "How many pipelines did you build?"
        if incognito:
            assert history._db.execute("SELECT count(*) FROM interview_sessions").fetchone()[0] == 0
        wizard._cancel()
        assert wizard._temporary_resumes == {}
    finally:
        history.close()


def test_failed_import_blocks_old_resume_until_user_explicitly_selects(
    qtbot, tmp_path, monkeypatch
):
    library = ResumeLibrary(tmp_path / "resumes.json")
    library.add("old.txt", "Old resume")
    wizard = SessionDialog(library)
    qtbot.addWidget(wizard)
    wizard.company.setText("Example")
    wizard.resume_combo.activated.emit(wizard.resume_combo.findData("old.txt"))
    attach(qtbot, monkeypatch, wizard, tmp_path / "missing.pdf")
    with pytest.raises(ValueError, match="Resume attachment failed"):
        wizard.collect()
    wizard.next_btn.click()
    assert wizard.pages.currentIndex() == 0
    assert "Retry" in wizard.message.text()
    assert "Could not read" in wizard.resume_status.text()
    wizard.resume_combo.activated.emit(wizard.resume_combo.findData("old.txt"))
    assert wizard.collect().resume_override == "Old resume"


@pytest.mark.parametrize("cancel", [True, False])
def test_wizard_import_is_responsive_and_private_across_privacy_change(
    qtbot, tmp_path, monkeypatch, cancel
):
    release = threading.Event()
    writable = False
    library = ResumeLibrary(tmp_path / "resumes.json", can_write=lambda: writable)
    wizard = SessionDialog(library)
    qtbot.addWidget(wizard)
    wizard.company.setText("Example")

    def slow_read(path):
        release.wait(3)
        return "Private resume text"

    monkeypatch.setattr("interview_assistant.ui.resume_page.read_attachment", slow_read)
    monkeypatch.setattr(
        "interview_assistant.ui.session_dialog.QFileDialog.getOpenFileName",
        lambda *a, **k: (str(tmp_path / "new.txt"), ""),
    )
    wizard.attach_new_resume()
    try:
        ticks = []
        QTimer.singleShot(0, lambda: ticks.append(True))
        qtbot.waitUntil(lambda: bool(ticks))
        assert not wizard.next_btn.isEnabled()
        with pytest.raises(ValueError, match="finish loading"):
            wizard.collect()
        writable = True
        if cancel:
            wizard._cancel()
    finally:
        release.set()
        qtbot.waitUntil(lambda: wizard._resume_worker is None)
    assert not (tmp_path / "resumes.json").exists()
    if cancel:
        assert wizard._resume_texts == {}
        assert wizard.resume_status.text() == ""
    else:
        assert wizard.collect().resume_override == "Private resume text"
        wizard.refresh_resumes()
        assert wizard.collect().resume_override == "Private resume text"


def test_incognito_resume_page_opens_session_only_attach(qtbot, tmp_path, monkeypatch):
    library = ResumeLibrary(tmp_path / "resumes.json", can_write=lambda: False)
    history = HistoryStore(":memory:")
    screen = StartScreen(history, library, lambda d: None, lambda: None, lambda: None)
    qtbot.addWidget(screen)
    path = tmp_path / "resume.txt"
    path.write_text("New private resume", encoding="utf-8")
    monkeypatch.setattr(
        "interview_assistant.ui.session_dialog.QFileDialog.getOpenFileName",
        lambda *a, **k: (str(path), ""),
    )
    try:
        screen.resume_page.session_import_btn.click()
        qtbot.waitUntil(lambda: screen.create_page._resume_worker is None)
        assert screen.pages.currentWidget() is screen.create_page
        screen.create_page.company.setText("Example")
        assert screen.create_page.collect().resume_override == "New private resume"
        assert not (tmp_path / "resumes.json").exists()
    finally:
        history.close()


def test_resume_flow_keeps_compact_single_window(qtbot, tmp_path, monkeypatch):
    app = QApplication.instance()
    apply_theme(app)
    library = ResumeLibrary(tmp_path / "resumes.json", can_write=lambda: False)
    history = HistoryStore(":memory:")
    screen = StartScreen(history, library, lambda d: None, lambda: None, lambda: None)
    qtbot.addWidget(screen)
    path = tmp_path / "Synthetic Data Engineer Resume.txt"
    path.write_text("Project Cedar: 12 pipelines", encoding="utf-8")
    monkeypatch.setattr(
        "interview_assistant.ui.session_dialog.QFileDialog.getOpenFileName",
        lambda *a, **k: (str(path), ""),
    )
    try:
        screen.resize(960, 680)
        screen.show()
        screen._show_resumes()
        qtbot.wait(100)
        assert screen.resume_page.session_import_btn.isVisible()
        assert not screen.resume_page.import_btn.isEnabled()
        screen.grab().save(str(tmp_path / "private-resume-library.png"))
        screen.resume_page.session_import_btn.click()
        qtbot.waitUntil(lambda: screen.create_page._resume_worker is None)
        qtbot.wait(100)
        assert screen.size().width() == 960 and screen.size().height() == 680
        assert screen.create_page.resume_status.isVisible()
        assert screen.create_page.resume_status.window() is screen
        viewport = screen.create_page.pages.widget(0).viewport()
        assert viewport.rect().contains(
            screen.create_page.resume_status.mapTo(
                viewport, screen.create_page.resume_status.rect().center()
            )
        )
        assert [w for w in app.topLevelWidgets() if w.isVisible()] == [screen]
        screen.grab().save(str(tmp_path / "private-session-attachment.png"))
    finally:
        history.close()
