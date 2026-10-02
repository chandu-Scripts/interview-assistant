import pytest

from interview_assistant.agents.verify import CLAIM_PRONE, find_unverified_claims


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ("We saw a 20% increase in efficiency.", ["20%"]),
        ("We saved $50k in the first quarter.", ["$50k"]),
        ("Costs dropped by 12.5% and we saved £2m.", ["12.5%", "£2m"]),
        ("A hash map gives O(1) average lookup.", []),  # not percent/money shaped
        ("", []),
        ("   ", []),
    ],
)
def test_finds_numeric_claims(answer, expected):
    assert find_unverified_claims(answer, context="") == expected


def test_claim_present_in_context_is_not_flagged():
    ctx = "Reduced latency by 20% at my last job."
    assert find_unverified_claims("We saw a 20% increase.", ctx) == []


def test_claim_present_via_digits_only_is_not_flagged():
    # context mentions "20 percent" (no %) - the digits still match, so it's not flagged.
    assert find_unverified_claims("Improved throughput by 20%.", "roughly 20 percent faster") == []


def test_only_unmatched_claims_are_returned():
    ctx = "Saved $50k on infrastructure."
    found = find_unverified_claims("We saved $50k and grew revenue by 30%.", ctx)
    assert found == ["30%"]


def test_duplicate_claims_reported_once():
    assert find_unverified_claims("20% here, 20% there, 20% everywhere.", "") == ["20%"]


def test_context_matching_is_case_insensitive():
    assert find_unverified_claims("Improved by 20%", "IMPROVED BY 20%") == []


def test_claim_prone_categories():
    assert CLAIM_PRONE == {"behavioral", "hr", "general"}
