"""Saved verbatim answers must not cross reversed entity roles, even at high similarity."""

import numpy as np
import pytest

from interview_assistant.app.practice_library import PracticeLibrary
from interview_assistant.app.practice_matcher import PracticeMatcher, terms


class SimilarEncoder:
    def __init__(self):
        self.calls = []

    def encode(self, texts):
        self.calls.append(texts)
        return np.ones((len(texts), 1), dtype=np.float32)


@pytest.fixture
def direction_matcher(tmp_path):
    library = PracticeLibrary(tmp_path / "qa.json", lambda: True)
    encoder = SimilarEncoder()
    return PracticeMatcher(library, lambda: encoder), encoder


@pytest.mark.parametrize(
    "saved, query",
    [
        (
            "How did you migrate data from SQL Server to Snowflake?",
            "How did you migrate data from Snowflake to SQL Server?",
        ),
        (
            "How did you move data from Azure SQL to Azure Synapse?",
            "How did you move data from Azure Synapse to Azure SQL?",
        ),
        (
            "How does the scheduler trigger the worker?",
            "How does the worker trigger the scheduler?",
        ),
        (
            "Why did you choose Python instead of Java?",
            "Why did you choose Java instead of Python?",
        ),
        (
            "Why did ingestion run before validation?",
            "Why did validation run before ingestion?",
        ),
        (
            "How did you copy SQL Server data into Snowflake?",
            "How did you copy Snowflake data into SQL Server?",
        ),
    ],
)
def test_equal_bags_do_not_bypass_order_or_semantic_guard(direction_matcher, saved, query):
    matcher, encoder = direction_matcher
    matcher.library.save(None, saved, "Original saved answer")
    assert terms(saved) == terms(query)  # The old shortcut returned the wrong answer here.
    assert matcher.lookup(query).answer is None
    assert encoder.calls  # Also reject the maximally similar semantic fallback.
    assert matcher.lookup(query).answer is None  # Cached abstention stays safe.


@pytest.mark.parametrize(
    "query",
    [
        "How did you move data from Snowflake into SQL Server?",
        "How did you transfer data to SQL Server from Snowflake?",
        "How did you move data from SQL Server to PostgreSQL?",
        "How did you transfer data from PostgreSQL to Snowflake?",
    ],
)
def test_semantic_similarity_does_not_override_direction_or_endpoint(direction_matcher, query):
    matcher, encoder = direction_matcher
    saved = "How did you migrate data from SQL Server to Snowflake?"
    matcher.library.save(None, saved, "Original saved answer")
    assert terms(saved) != terms(query)
    assert matcher.lookup(query).answer is None
    assert encoder.calls


@pytest.mark.parametrize(
    "query",
    [
        "How did you migrate data from SQL Server to Snowflake?",
        "How did you migrate data into Snowflake from SQL Server?",
        "How did you move data from SQL Server into Snowflake?",
        "How did you transfer data from Microsoft SQL Server to Snowflake using ADF?",
    ],
)
def test_same_direction_paraphrases_return_saved_answer_unchanged(direction_matcher, query):
    matcher, _ = direction_matcher
    answer = "First paragraph.\n\nSELECT * FROM source_table;"
    matcher.library.save(None, "How did you migrate data from SQL Server to Snowflake?", answer)
    before = matcher.library.path.read_bytes()
    assert matcher.lookup(query).answer.answer == answer
    assert matcher.library.path.read_bytes() == before


def test_semantic_filter_selects_correct_direction_before_margin(direction_matcher):
    matcher, _ = direction_matcher
    matcher.library.save(
        None, "How did you migrate data from SQL Server to Snowflake?", "SQL Server to Snowflake"
    )
    matcher.library.save(
        None, "How did you migrate data from Snowflake to SQL Server?", "Snowflake to SQL Server"
    )
    result = matcher.lookup("How did you move data into Snowflake from SQL Server?")
    assert result.answer.answer == "SQL Server to Snowflake"


def test_ordered_shortcut_keeps_normal_stopword_paraphrase_without_model(direction_matcher):
    matcher, encoder = direction_matcher
    matcher.library.save(None, "How many pipelines did you build?", "Six pipelines.")
    result = matcher.lookup("How many pipelines have you built in your project?")
    assert result.answer.answer == "Six pipelines."
    assert encoder.calls == []


def test_common_name_fragment_does_not_establish_same_endpoint(direction_matcher):
    matcher, _ = direction_matcher
    matcher.library.save(
        None, "How did you move data from Azure SQL to Azure Synapse?", "Original saved answer"
    )
    assert (
        matcher.lookup("How did you move data from Azure Databricks to Azure Synapse?").answer
        is None
    )


@pytest.mark.parametrize(
    "saved, query",
    [
        ("A -> B", "A <- B"),
        ("A \u2192 B", "A \u2190 B"),
        ("SQL Server -> Snowflake", "SQL Server <- Snowflake"),
        ("A -> B -> C", "A <- B <- C"),
        ("SQL Server -> Snowflake", "SQL Server Snowflake"),
    ],
)
def test_normalized_exact_cannot_erase_directional_operator(direction_matcher, saved, query):
    matcher, _ = direction_matcher
    matcher.library.save(None, saved, "Original saved answer")
    assert matcher.lookup(query).answer is None


@pytest.mark.parametrize(
    "saved, query",
    [
        ("A -> B", "A -> B"),
        ("A -> B", "A \u2192 B"),
        ("A -> B", "B <- A"),
        ("A -> B -> C", "A -> B -> C"),
        ("SQL Server -> Snowflake", "How did you move data from SQL Server to Snowflake?"),
        ("How did you move data from SQL Server to Snowflake?", "SQL Server -> Snowflake"),
    ],
)
def test_equivalent_symbolic_directions_remain_usable(direction_matcher, saved, query):
    matcher, _ = direction_matcher
    matcher.library.save(None, saved, "Original saved answer")
    assert matcher.lookup(query).answer.answer == "Original saved answer"


def test_semantic_cache_does_not_reuse_answer_for_opposite_arrow(direction_matcher):
    matcher, encoder = direction_matcher
    matcher.library.save(
        None, "How did you migrate data from SQL Server to Snowflake?", "Original saved answer"
    )
    forward = "How did you move data SQL Server -> Snowflake?"
    reverse = "How did you move data SQL Server <- Snowflake?"
    assert matcher.lookup(forward).answer is not None
    assert len(encoder.calls) == 2
    assert matcher.lookup(reverse).answer is None
    assert len(encoder.calls) == 3
    assert matcher.lookup(forward).answer is not None
    assert matcher.lookup(reverse).answer is None
    assert len(encoder.calls) == 3
