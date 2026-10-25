"""Offline model integration and deterministic retrieval failure/ambiguity tests."""

import numpy as np
import pytest

from interview_assistant.app.practice_library import PracticeLibrary
from interview_assistant.app.practice_matcher import PracticeMatcher
from interview_assistant.app.semantic_encoder import MODEL_DIR


class FakeEncoder:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def encode(self, texts):
        self.calls.append(list(texts))
        return np.array([self.rows[t] for t in texts], dtype=np.float32)


def test_semantic_cache_edit_delete_and_unrelated(tmp_path):
    library = PracticeLibrary(tmp_path / "qa.json", lambda: True)
    entry = library.save(None, "What was your team size?", "Original saved answer")
    encoder = FakeEncoder(
        {
            "What was your team size?": [1, 0],
            "How big was your engineering group?": [0.9, 0.436],
            "Explain indexes": [0, 1],
        }
    )
    matcher = PracticeMatcher(library, lambda: encoder)
    question = "How big was your engineering group?"
    assert matcher.lookup(question).answer.answer == "Original saved answer"
    assert matcher.lookup(question).answer.answer == "Original saved answer"
    assert len(encoder.calls) == 2  # saved index once, query once
    assert matcher.lookup("Explain indexes").answer is None
    library.save(entry, "What was your team size?", "Updated answer")
    assert matcher.lookup(question).answer.answer == "Updated answer"
    library.delete(entry)
    assert matcher.lookup(question).answer is None


def test_competing_answers_and_low_confidence_abstain(tmp_path):
    library = PracticeLibrary(tmp_path / "qa.json", lambda: True)
    library.save(None, "First saved question", "First answer")
    library.save(None, "Second saved question", "Conflicting answer")
    encoder = FakeEncoder(
        {
            "First saved question": [1, 0],
            "Second saved question": [0.98, 0.199],
            "An ambiguous paraphrase": [1, 0],
            "Unrelated question": [0, 1],
        }
    )
    matcher = PracticeMatcher(library, lambda: encoder)
    assert matcher.lookup("An ambiguous paraphrase").answer is None
    assert matcher.lookup("Unrelated question").answer is None


def test_missing_model_keeps_exact_matching_and_attempts_loading_once(tmp_path):
    library = PracticeLibrary(tmp_path / "qa.json", lambda: True)
    library.save(None, "Saved question", "Saved answer")
    calls = []

    def broken():
        calls.append(1)
        raise OSError("Model missing")

    matcher = PracticeMatcher(library, broken)
    assert matcher.lookup("A different question").answer is None
    assert matcher.lookup("Something else").answer is None
    assert calls == [1]
    assert matcher.lookup("Saved question").answer.answer == "Saved answer"


@pytest.fixture(scope="module")
def local_matcher(tmp_path_factory):
    if not (MODEL_DIR / "model.onnx").exists():
        pytest.skip("Run scripts/download_semantic_model.py for offline model integration tests")
    library = PracticeLibrary(tmp_path_factory.mktemp("semantic") / "qa.json", lambda: True)
    library.add_examples()
    matcher = PracticeMatcher(library)
    matcher.prepare()
    return matcher


@pytest.mark.parametrize(
    "question, topic",
    [
        ("What was your catalog count?", "catalogs"),
        ("How many catalogs u have sed?", "catalogs"),
        ("How many Unity Catalog catalogs did your team maintain?", "catalogs"),
        ("What volume of data did you process each day?", "data"),
        ("How big was the team you worked with?", "team"),
        ("How many people were in your project team?", "team"),
        ("What is the number of pipelines you developed?", "pipelines"),
    ],
)
def test_real_model_paraphrases(local_matcher, question, topic):
    result = local_matcher.lookup(question)
    assert result.answer is not None
    assert topic in result.answer.question


@pytest.mark.parametrize(
    "question",
    [
        "What is Unity Catalog?",
        "How did you organize your catalogs?",
        "Why did you use catalogs?",
        "How many catalogs did you NOT use?",
        "How many catalogs did another team use?",
        "How many catalogs and pipelines did you use?",
        "How many catalogs did you use before migration?",
        "How many catalogs did you use in production?",
        "How many catalogs did you use in dev?",
        "How many catalogs didn't you use?",
        "How many catalogs did you use? Why did you need them?",
        "How many tables were in the catalog?",
        "How many schemas did you use?",
        "How did you choose your team size?",
        "What was the size of your company?",
        "Compare a Python list with a tuple.",
        "How do I build a data pipeline?",
    ],
)
def test_real_model_changed_meaning(local_matcher, question):
    assert local_matcher.lookup(question).answer is None


def test_scope_filter_runs_before_semantic_margin(tmp_path):
    library = PracticeLibrary(tmp_path / "qa.json", lambda: True)
    generic = "How many catalogs did you use?"
    scoped = "How many catalogs did you use in production?"
    query = "What was your prod catalog count?"
    library.save(None, generic, "Three across all environments")
    library.save(None, scoped, "One in production")
    encoder = FakeEncoder({generic: [1, 0], scoped: [0.95, 0.312], query: [1, 0]})
    match = PracticeMatcher(library, lambda: encoder).lookup(query)
    assert match.answer.answer == "One in production"


def test_identical_paraphrase_answers_do_not_compete(tmp_path):
    library = PracticeLibrary(tmp_path / "qa.json", lambda: True)
    first = "What was your team size?"
    second = "How big was your team?"
    query = "How many people worked in the team?"
    for q in (first, second):
        library.save(None, q, "Six members.\n\nThree data engineers.")
    encoder = FakeEncoder({first: [1, 0], second: [0.99, 0.14], query: [1, 0]})
    matcher = PracticeMatcher(library, lambda: encoder)
    assert matcher.lookup(query).answer.answer == "Six members.\n\nThree data engineers."


def test_real_model_selects_scope_and_keeps_generic_count_separate(local_matcher, tmp_path):
    library = PracticeLibrary(tmp_path / "qa.json", lambda: True)
    library.save(None, "How many catalogs did you use?", "Three overall.")
    library.save(None, "How many catalogs did you use in production?", "One production catalog.")
    library.save(None, "How many catalogs did you use in development?", "One development catalog.")
    matcher = PracticeMatcher(library, lambda: local_matcher._encoder)
    for query, expected in (
        ("What was your prod catalog count?", "One production catalog."),
        ("How many catalogs did you use in dev?", "One development catalog."),
        ("What was your catalog count?", "Three overall."),
    ):
        result = matcher.lookup(query)
        assert result.answer is not None, query
        assert result.answer.answer == expected
