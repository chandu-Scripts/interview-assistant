import threading
from pathlib import Path

import pytest

from interview_assistant.agents.jev import JevConfig
from interview_assistant.app.interview_session import InterviewSessionManager
from interview_assistant.app.resume_library import ResumeLibrary
from interview_assistant.history.store import HistoryStore
from interview_assistant.ui.resume_page import ResumePage
from interview_assistant.ui.start_screen import StartScreen


def test_import_preview_duplicate_and_unreadable_file(qtbot, tmp_path):
    library = ResumeLibrary(tmp_path / "resumes.json")
    page = ResumePage(library)
    qtbot.addWidget(page)
    file = tmp_path / "resume.txt"
    file.write_text("Engineer <b>literal text</b>", encoding="utf-8")
    page.import_file(file)
    qtbot.waitUntil(lambda: page._worker is None)
    assert page.preview.toPlainText() == "Engineer <b>literal text</b>"
    assert page.rename_btn.isEnabled()
    file.write_text("Replacement", encoding="utf-8")
    page.import_file(file)
    qtbot.waitUntil(lambda: page._worker is None)
    assert "already exists" in page.message.text()
    assert library.list()[0].text == "Engineer <b>literal text</b>"
    page.import_file(tmp_path / "missing.pdf")
    qtbot.waitUntil(lambda: page._worker is None)
    assert "Could not read" in page.message.text()
    assert page.import_btn.isEnabled()


def test_resume_management_refreshes_wizard_and_preserves_session_copy(
    qtbot, tmp_path, monkeypatch
):
    library = ResumeLibrary(tmp_path / "resumes.json")
    library.add("cv.txt", "Original resume")
    store = HistoryStore(":memory:")
    manager = InterviewSessionManager(JevConfig(), store, None)
    screen = StartScreen(store, library, manager.activate, lambda: None, lambda: None)
    qtbot.addWidget(screen)
    wizard = screen.create_page
    wizard.company.setText("Northwind")
    index = wizard.resume_combo.findData("cv.txt")
    wizard.resume_combo.setCurrentIndex(index)
    wizard.resume_combo.activated.emit(index)
    session = manager.activate(wizard.collect())
    screen.resumes_btn.click()
    page = screen.resume_page
    assert screen.pages.currentWidget() is page
    monkeypatch.setattr(
        "interview_assistant.ui.resume_page.QInputDialog.getText",
        lambda *a, **k: ("Platform CV", True),
    )
    page.rename_btn.click()
    assert page.selected_name() == "Platform CV"
    assert wizard.resume_combo.findData("Platform CV") >= 0
    assert wizard.collect().resume_override == ""
    page._confirm_delete = lambda name: False
    page.delete_btn.click()
    assert len(library.list()) == 1
    page._confirm_delete = lambda name: True
    page.delete_btn.click()
    assert library.list() == [] and page.preview.toPlainText() == ""
    assert not page.delete_btn.isEnabled()
    assert store.get_interview_session(session.id).resume_override == "Original resume"
    store.close()


def test_rename_rejects_collision_and_blank_and_keeps_metadata(tmp_path):
    library = ResumeLibrary(tmp_path / "resumes.json")
    first = library.add("one", "text")
    library.add("two", "other")
    for name in ("TWO", "  "):
        with pytest.raises(ValueError):
            library.rename("one", name)
    library.rename("one", "ONE")
    renamed = next(r for r in library.list() if r.name == "ONE")
    assert (renamed.text, renamed.last_used) == (first.text, first.last_used)


@pytest.mark.parametrize("data", ["{broken", "{}", '[{"name": 4}]'])
def test_damaged_library_is_visible_and_never_overwritten(qtbot, tmp_path, data):
    path = tmp_path / "resumes.json"
    path.write_text(data, encoding="utf-8")
    library = ResumeLibrary(path)
    page = ResumePage(library)
    qtbot.addWidget(page)
    assert "preserved" in page.message.text()
    with pytest.raises(ValueError):
        library.add("new", "text")
    assert path.read_text(encoding="utf-8") == data


def test_atomic_save_failure_preserves_library(tmp_path, monkeypatch):
    path = tmp_path / "resumes.json"
    library = ResumeLibrary(path)
    library.add("one", "text")
    previous = path.read_bytes()

    def fail(*args):
        raise OSError("Disk unavailable")

    monkeypatch.setattr(Path, "replace", fail)
    with pytest.raises(OSError):
        library.rename("one", "new")
    assert path.read_bytes() == previous
    assert not list(tmp_path.glob(".resumes-*.tmp"))


def test_import_keeps_ui_responsive(qtbot, tmp_path, monkeypatch):
    from PySide6.QtCore import QTimer

    release = threading.Event()

    def slow_read(path):
        release.wait(3)
        return "Extracted text"

    monkeypatch.setattr("interview_assistant.ui.resume_page.read_attachment", slow_read)
    page = ResumePage(ResumeLibrary(tmp_path / "resumes.json"))
    qtbot.addWidget(page)
    ticks = []
    page.import_file(tmp_path / "resume.txt")
    try:
        QTimer.singleShot(0, lambda: ticks.append(True))
        qtbot.waitUntil(lambda: bool(ticks))
        assert not page.import_btn.isEnabled()
        assert page._worker is not None and page._worker.isRunning()
    finally:
        release.set()
        qtbot.waitUntil(lambda: page._worker is None)


def test_wizard_replacement_requires_confirmation(qtbot, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from interview_assistant.ui.session_dialog import ATTACH_NEW, SessionDialog

    library = ResumeLibrary(tmp_path / "resumes.json")
    library.add("cv.txt", "Original")
    path = tmp_path / "cv.txt"
    path.write_text("Replacement", encoding="utf-8")
    monkeypatch.setattr(
        "interview_assistant.ui.session_dialog.QFileDialog.getOpenFileName",
        lambda *a, **k: (str(path), ""),
    )
    wizard = SessionDialog(library)
    qtbot.addWidget(wizard)
    for response, expected in (
        (QMessageBox.StandardButton.No, "Original"),
        (QMessageBox.StandardButton.Yes, "Replacement"),
    ):
        monkeypatch.setattr(QMessageBox, "exec", lambda self, r=response: r)
        wizard.resume_combo.activated.emit(wizard.resume_combo.findData(ATTACH_NEW))
        assert library.list()[0].text == expected
