import time

import pytest

from interview_assistant.app.practice_library import PracticeLibrary
from interview_assistant.app.practice_matcher import PracticeMatcher, terms


@pytest.fixture
def matcher(tmp_path):
    library = PracticeLibrary(tmp_path / "qa.json", lambda: True)
    library.add_examples()
    return PracticeMatcher(library)


@pytest.mark.parametrize(
    "question",
    [
        "HOW MANY CATALOGS DID YOU USE?!",
        "How many catalogs have you used in your project?",
        "How many catalogs u have sed?",
        "How many catalogues have you used?",
        "How many pipelines have you built?",
        "What was your team size?",
    ],
)
def test_clear_matches(matcher, question):
    assert matcher.lookup(question).answer is not None


@pytest.mark.parametrize(
    "question",
    [
        "What is the difference between a list and a tuple?",
        "What is a catalog?",
        "How do you build a pipeline?",
        "How many catalogs did you NOT use?",
        "How many catalogs did another team use?",
        "How many catalogs and pipelines did you use?",
    ],
)
def test_no_automatic_match_for_unrelated_or_changed_meaning(matcher, question):
    assert matcher.lookup(question).answer is None


def test_uncertainty_cache_updates_and_duplicate_questions(matcher):
    assert matcher.lookup("How many ADF pipelines did you build?").answer is not None
    question = "What was your team size?"
    entry = matcher.lookup(question).answer
    matcher.library.save(entry.id, question, "Changed answer")
    assert matcher.lookup(question).answer.answer == "Changed answer"
    with pytest.raises(ValueError, match="already saved"):
        matcher.library.save(None, question, "Conflicting answer")
    assert matcher.lookup(question).answer.answer == "Changed answer"
    for entry in matcher.library.entries():
        matcher.library.delete(entry.id)
    assert matcher.lookup(question).answer is None


def test_conflicting_catalog_answers_need_resolution(matcher):
    duplicate = matcher.library.save(
        None, "How many catalogs did you use in your project?", "Different saved answer"
    )
    assert matcher.lookup("How many catalogs u have sed?").answer is None
    matcher.library.delete(duplicate)
    assert matcher.lookup("How many catalogs u have sed?").answer is not None


def test_sed_tool_is_not_globally_removed():
    assert "sed" in terms("How have you used sed in your project?")


@pytest.mark.parametrize("same_answer", [True, False])
def test_legacy_duplicates_are_preserved_and_only_agreed_answers_return(tmp_path, same_answer):
    import json

    library = PracticeLibrary(tmp_path / "qa.json", lambda: True)
    records = [
        {"id": "1", "question": "Team size?", "answer": "Six members"},
        {"id": "2", "question": "TEAM SIZE!", "answer": "Six members" if same_answer else "Ten"},
    ]
    library.path.write_text(json.dumps(records), encoding="utf-8")
    before = library.path.read_bytes()
    result = PracticeMatcher(library).lookup("Team size?")
    assert (result.answer is not None) == same_answer
    assert library.path.read_bytes() == before


def test_lookup_timing_with_thousand_questions(matcher):
    import json

    matcher.library.import_json(
        json.dumps({f"Practice question {i}?": f"Answer {i}" for i in range(1000)})
    )
    matcher.lookup("Practice question 500?")
    start = time.perf_counter()
    for _ in range(100):
        assert matcher.lookup("Practice question 500?").answer.answer == "Answer 500"
    print(f"Warm exact lookup, 1004 entries: {(time.perf_counter() - start) * 10:.3f} ms average")
