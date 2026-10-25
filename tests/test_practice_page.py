from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox

from interview_assistant.app.practice_library import PracticeLibrary
from interview_assistant.ui.practice_page import PracticePage


def test_persistence_updates_examples_and_delete(tmp_path):
    path = tmp_path / "qa.json"
    library = PracticeLibrary(path, lambda: True)
    assert library.entries() == [] and not path.exists()
    library.add_examples()
    library.add_examples()
    assert len(library.entries()) == 4
    entry = library.entries()[0]
    library.save(entry.id, entry.question, "Edited example")
    assert PracticeLibrary(path, lambda: True).entries()[0].answer == "Edited example"
    library.delete(entry.id)
    assert len(library.entries()) == 3


def test_corruption_and_failed_save_preserve_data(tmp_path, monkeypatch):
    path = tmp_path / "qa.json"
    library = PracticeLibrary(path, lambda: True)
    library.save(None, "Question?", "Example answer")
    before = path.read_bytes()

    def fail(*args):
        raise OSError("Disk unavailable")

    monkeypatch.setattr(Path, "replace", fail)
    with pytest.raises(OSError):
        library.save(None, "Another?", "Example")
    assert path.read_bytes() == before
    assert not list(tmp_path.glob(".practice-*.tmp"))
    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(ValueError, match="preserved"):
        library.add_examples()
    assert path.read_text(encoding="utf-8") == "{broken"


def test_incognito_blocks_every_mutation(tmp_path):
    writable = True
    library = PracticeLibrary(tmp_path / "qa.json", lambda: writable)
    entry_id = library.save(None, "Question?", "Example")
    before = library.path.read_bytes()
    writable = False
    for operation in (
        lambda: library.save(None, "New?", "Example"),
        lambda: library.save(entry_id, "Question?", "Changed"),
        lambda: library.delete(entry_id),
        library.add_examples,
    ):
        with pytest.raises(ValueError, match="Incognito"):
            operation()
    assert library.path.read_bytes() == before


def test_duplicate_create_and_rename_preserve_saved_answers(tmp_path):
    library = PracticeLibrary(tmp_path / "qa.json", lambda: True)
    first = library.save(None, "Team size?", "Six members")
    second = library.save(None, "Pipeline count?", "Twenty")
    before = library.path.read_bytes()
    for ident in (None, second):
        with pytest.raises(ValueError, match="already saved"):
            library.save(ident, " TEAM SIZE!! ", "Conflicting answer")
        assert library.path.read_bytes() == before
    library.save(first, "Team size?", "Updated answer")
    assert library.entries()[0].answer == "Updated answer"


def test_technical_names_are_not_collapsed_as_duplicates(tmp_path):
    library = PracticeLibrary(tmp_path / "qa.json", lambda: True)
    for name in ("C", "C++", "C#", "Python 3.11", "Python 3.12"):
        library.save(None, f"What is {name}?", f"About {name}")
    assert len(library.entries()) == 5


def test_duplicate_save_shows_inline_warning_and_keeps_draft(qtbot, tmp_path):
    library = PracticeLibrary(tmp_path / "qa.json", lambda: True)
    library.save(None, "Team size?", "Six members")
    page = PracticePage(library)
    qtbot.addWidget(page)
    page.show()
    page.new_btn.click()
    page.question.setText("TEAM SIZE!")
    page.answer.setPlainText("A different draft")
    page.save_btn.click()
    assert "already saved" in page.message.text()
    assert page.answer.toPlainText() == "A different draft"
    assert len(library.entries()) == 1
    assert library.entries()[0].answer == "Six members"


def test_edit_search_delete_and_privacy(qtbot, tmp_path, monkeypatch):
    writable = True
    page = PracticePage(PracticeLibrary(tmp_path / "qa.json", lambda: writable))
    qtbot.addWidget(page)
    page.show()
    assert not page.answer.isVisible() and not page.detail_answer.isVisible()
    page.new_btn.click()
    page.save_btn.click()
    assert "question before saving" in page.message.text()
    page.cancel_btn.click()
    page.examples_btn.click()
    assert page.entries.count() == 4
    page.search.setText("CATALOGS")
    assert page.entries.count() == 1
    page.entries.setCurrentRow(0)
    assert not page.detail_answer.isVisible()
    qtbot.keyClick(page.entries, Qt.Key.Key_Return)
    assert "three catalogs" in page.detail_answer.toPlainText()
    assert not page.answer.isVisible()
    page.edit_btn.click()
    page.answer.setPlainText("Changed fictional answer")
    page.save_btn.click()
    assert page.library.entries()[0].answer == "Changed fictional answer"
    assert page.stack.currentWidget() is page.list_view
    page.entries.setCurrentRow(0)
    page._select()
    writable = False
    page.update_privacy()
    assert page.notice.isVisible()
    assert not page.save_btn.isEnabled() and not page.delete_btn.isEnabled()
    with qtbot.waitSignal(page.privacy_requested):
        page.privacy_btn.click()
    writable = True
    page.update_privacy()
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.StandardButton.No)
    page.delete_btn.click()
    assert len(page.library.entries()) == 4
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.StandardButton.Yes)
    page.delete_btn.click()
    assert len(page.library.entries()) == 3
    page.new_btn.click()
    page.question.setText("A new question?")
    page.answer.setPlainText("A fictional answer")
    page.save_btn.click()
    assert len(page.library.entries()) == 4


@pytest.mark.parametrize("answer", ["", "   \n\t"])
def test_missing_answer_never_writes_and_keeps_form_open(qtbot, tmp_path, answer):
    path = tmp_path / "qa.json"
    page = PracticePage(PracticeLibrary(path, lambda: True))
    qtbot.addWidget(page)
    page.show()
    page.new_btn.click()
    page.question.setText("How many pipelines?")
    page.answer.setPlainText(answer)
    page.save_btn.click()
    assert "answer is compulsory" in page.message.text()
    assert not path.exists()
    assert page.stack.currentWidget() is page.editor_view
    assert page.question.text() == "How many pipelines?"


def test_cancel_preserves_saved_answer_and_rapid_navigation(qtbot, tmp_path, monkeypatch):
    library = PracticeLibrary(tmp_path / "qa.json", lambda: True)
    library.add_examples()
    original = library.path.read_bytes()
    page = PracticePage(library)
    qtbot.addWidget(page)
    page.show()
    item = page.entries.item(0)
    qtbot.mouseClick(
        page.entries.viewport(),
        Qt.MouseButton.LeftButton,
        pos=page.entries.visualItemRect(item).center(),
    )
    assert page.stack.currentWidget() is page.detail_view
    page.edit_btn.click()
    page.answer.clear()
    page.save_btn.click()
    assert library.path.read_bytes() == original
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.StandardButton.No)
    page.cancel_btn.click()
    assert page.stack.currentWidget() is page.editor_view
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.StandardButton.Yes)
    page.cancel_btn.click()
    assert page.stack.currentWidget() is page.detail_view
    for _ in range(5):
        page.back_btn.click()
        page.entries.setCurrentRow(0)
        page._select()
    page.back_btn.click()
    qtbot.waitUntil(lambda: page._opacity.opacity() == 1)
    assert page.stack.currentWidget() is page.list_view
    assert not page.detail_answer.isVisible()
    assert library.path.read_bytes() == original
