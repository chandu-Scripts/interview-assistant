import pytest

from interview_assistant.app.resume_library import ResumeLibrary


def test_incognito_preserves_library_and_does_not_touch_usage_time(tmp_path):
    path = tmp_path / "resumes.json"
    allowed = True
    library = ResumeLibrary(path, can_write=lambda: allowed)
    library.add("existing.txt", "Resume")
    before = path.read_bytes()
    allowed = False
    library.touch("existing.txt")
    assert library.list()[0].text == "Resume"
    with pytest.raises(ValueError, match="incognito"):
        library.add("new.txt", "Private resume")
    with pytest.raises(ValueError, match="incognito"):
        library.rename("existing.txt", "renamed.txt")
    assert path.read_bytes() == before


def test_attached_resumes_are_remembered_most_recent_first(tmp_path, monkeypatch):
    clock = iter([100.0, 200.0, 300.0])
    monkeypatch.setattr("interview_assistant.app.resume_library.time.time", lambda: next(clock))
    lib = ResumeLibrary(tmp_path / "resumes.json")
    lib.add("cv_azure.pdf", "Azure data engineer")
    lib.add("cv_genai.docx", "GenAI engineer")
    assert [r.name for r in lib.list()] == ["cv_genai.docx", "cv_azure.pdf"]
    lib.touch("cv_azure.pdf")
    assert [r.name for r in lib.list()] == ["cv_azure.pdf", "cv_genai.docx"]


def test_same_name_replaces_the_earlier_copy(tmp_path):
    lib = ResumeLibrary(tmp_path / "resumes.json")
    lib.add("cv.txt", "old")
    lib.add("cv.txt", "new")
    (only,) = lib.list()
    assert only.text == "new"


def test_missing_or_damaged_library_is_empty(tmp_path):
    path = tmp_path / "resumes.json"
    assert ResumeLibrary(path).list() == []
    path.write_text("not json", encoding="utf-8")
    assert ResumeLibrary(path).list() == []
