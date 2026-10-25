import json
from pathlib import Path

import pytest

from interview_assistant.app.practice_library import PracticeLibrary, parse_qa_json
from interview_assistant.ui.practice_page import PracticePage


@pytest.mark.parametrize(
    "text",
    [
        "{",
        "[]",
        "{}",
        '{"Q": ""}',
        '{"Q": "  "}',
        '{" ": "A"}',
        '{"Q": null}',
        '{"Q": 3}',
        '{"Q": ["A"]}',
        '{"Q": {"nested": "A"}}',
        '{"Q": "A", "Q": "B"}',
        '{"Q": "A", " q ": "B"}',
        '{"Question?": "A", "question!": "B"}',
        '{"???": "A"}',
        '{"Valid": "A", "Invalid": ""}',
    ],
)
def test_invalid_batch_never_changes_existing_data(tmp_path, text):
    library = PracticeLibrary(tmp_path / "qa.json", lambda: True)
    library.save(None, "Existing", "Keep this")
    before = library.path.read_bytes()
    with pytest.raises(ValueError):
        library.import_json(text)
    assert library.path.read_bytes() == before


def test_batch_trims_preserves_duplicates_and_saves_once(tmp_path, monkeypatch):
    library = PracticeLibrary(tmp_path / "qa.json", lambda: True)
    library.save(None, "Existing", "Keep this")
    payload = json.dumps({" existing ": "Do not replace", " New? ": " Answer\nline two "})
    saves = []
    real_write = library._write

    def write(entries):
        saves.append(entries)
        real_write(entries)

    monkeypatch.setattr(library, "_write", write)
    assert library.import_json(payload) == (1, 1)
    assert len(saves) == 1
    assert [(e.question, e.answer) for e in library.entries()] == [
        ("Existing", "Keep this"),
        ("New?", "Answer\nline two"),
    ]
    assert library.import_json(payload) == (0, 2)
    assert len(saves) == 1


def test_import_limits_and_bom():
    assert parse_qa_json('\ufeff{"Q": "A"}') == [("Q", "A")]
    for payload in (
        json.dumps({str(i): "A" for i in range(1001)}),
        json.dumps({"Q": "A" * 12001}),
        json.dumps({"Q" * 1001: "A"}),
        " " * 1_048_577,
    ):
        with pytest.raises(ValueError):
            parse_qa_json(payload)


def test_batch_atomic_failure_and_incognito(tmp_path, monkeypatch):
    writable = True
    library = PracticeLibrary(tmp_path / "qa.json", lambda: writable)
    library.save(None, "Existing", "Keep")
    before = library.path.read_bytes()
    writable = False
    with pytest.raises(ValueError, match="Incognito"):
        library.import_json('{"New": "A"}')
    writable = True

    def fail(*args):
        raise OSError("Disk unavailable")

    monkeypatch.setattr(Path, "replace", fail)
    with pytest.raises(OSError):
        library.import_json('{"New": "A", "Another": "B"}')
    assert library.path.read_bytes() == before
    assert not list(tmp_path.glob(".practice-*.tmp"))


def test_paste_validate_import_and_manual_entry_still_work(qtbot, tmp_path):
    writable = True
    page = PracticePage(PracticeLibrary(tmp_path / "qa.json", lambda: writable))
    qtbot.addWidget(page)
    page.show()
    page.json_btn.click()
    view = page.json_view
    assert view.isVisible() and not page.answer.isVisible()
    view.text.setPlainText('{"One?": "Answer", "Two?": ""}')
    view.validate_btn.click()
    assert "compulsory" in view.status.text() and not view.import_btn.isEnabled()
    assert not page.library.path.exists()
    view.text.setPlainText('{"One?": "Answer", "Two?": "Second answer"}')
    view.validate_btn.click()
    assert "2 new questions" in view.status.text()
    assert not page.library.path.exists()
    writable = False
    page.update_privacy()
    assert not view.import_btn.isEnabled()
    writable = True
    page.update_privacy()
    view.text.insertPlainText(" ")
    assert not view.import_btn.isEnabled()
    view.validate_btn.click()
    view.import_btn.click()
    assert page.stack.currentWidget() is page.list_view
    assert page.entries.count() == 2 and not page.detail_answer.isVisible()
    page.new_btn.click()
    page.question.setText("Manual question")
    page.answer.setPlainText("Manual answer")
    page.save_btn.click()
    assert page.entries.count() == 3


def test_file_import_and_load_error_preserve_draft(qtbot, tmp_path, monkeypatch):
    page = PracticePage(PracticeLibrary(tmp_path / "qa.json", lambda: True))
    qtbot.addWidget(page)
    file = tmp_path / "input.json"
    file.write_text('{"Question?": "Answer"}', encoding="utf-8-sig")
    monkeypatch.setattr(
        "interview_assistant.ui.practice_import.QFileDialog.getOpenFileName",
        lambda *a: (str(file), ""),
    )
    view = page.json_view
    view.file_btn.click()
    assert view.import_btn.isEnabled()
    draft = view.text.toPlainText()
    file.write_bytes(b"\xff\xfe")
    view.file_btn.click()
    assert view.text.toPlainText() == draft
    assert not view.import_btn.isEnabled()
    assert "Could not load" in view.status.text()
    view.validate_btn.click()
    view.import_btn.click()
    assert len(page.library.entries()) == 1


def test_import_form_opens_in_incognito_but_cannot_save(qtbot, tmp_path):
    writable = False
    page = PracticePage(PracticeLibrary(tmp_path / "qa.json", lambda: writable))
    qtbot.addWidget(page)
    page.show()
    assert page.json_btn.isEnabled()
    page.json_btn.click()
    view = page.json_view
    assert view.isVisible()
    assert view.privacy_notice.isVisible()
    view.text.setPlainText('{"Question?": "Answer"}')
    view.validate_btn.click()
    assert not view.import_btn.isEnabled()
    assert "Incognito" in view.import_btn.toolTip()
    assert not page.library.path.exists()
    writable = True
    page.update_privacy()
    assert not view.privacy_notice.isVisible()
    assert view.import_btn.isEnabled()
    view.import_btn.click()
    assert len(page.library.entries()) == 1


def test_import_preview_and_save_agree_on_punctuation_duplicates(qtbot, tmp_path):
    library = PracticeLibrary(tmp_path / "qa.json", lambda: True)
    library.save(None, "What was your team size?", "Keep this")
    page = PracticePage(library)
    qtbot.addWidget(page)
    view = page.json_view
    view.text.setPlainText('{"WHAT WAS YOUR TEAM SIZE!": "Never overwrite", "New?": "New answer"}')
    view.validate_btn.click()
    assert "1 new questions; 1 existing questions" in view.status.text()
    view.import_btn.click()
    assert len(library.entries()) == 2
    assert library.entries()[0].answer == "Keep this"
