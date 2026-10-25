import pytest
from scripts.evaluate_answer_style import check_coding, check_style


@pytest.mark.parametrize(
    "paragraph, count, passed",
    [
        ("A list is mutable.", 0, False),
        ("Yeah, a list is mutable.", 1, False),
        ("Yeah, a list can change. Mainly, use it for edits. Also, it keeps order.", 3, True),
        ("Yeah, so this mainly works. Also, I mean it is useful.", 5, False),
    ],
)
def test_style_check_counts_occurrences_without_rewriting(paragraph, count, passed):
    result = check_style(f"```text\n{paragraph}\n```")
    assert result["paragraphs"][0]["text"] == paragraph
    assert result["paragraphs"][0]["count"] == count
    assert result["filler_counts_pass"] is passed


def test_style_check_exempts_code_output_and_counts_each_paragraph():
    answer = (
        "```text\nApproach\nSo, scan once. Mainly, track the total. Also, return it.\n\n"
        "A second paragraph without markers.\n```\n"
        "```python\nprint('yeah so mainly \u2014')\n```\n"
        "```text\nOutput\nExpected, not executed.\n| value |\n|---|\n| yeah |\n```"
    )
    result = check_style(answer)
    assert [p["count"] for p in result["paragraphs"]] == [3, 0]
    assert not result["filler_counts_pass"]
    assert result["no_prohibited_dashes"]
    assert result["all_content_fenced"]
    assert not check_style(answer + "Outside a block")["all_content_fenced"]
    assert not check_style("```text\nYeah, a list \u2014 mainly for edits, also ordered.\n```")[
        "no_prohibited_dashes"
    ]


def test_coding_check_rejects_missing_structural_comments_and_explanations():
    answer = (
        "```text\nApproach\nScan the rows.\n```\n"
        "```sql\nSELECT 1 -- Line 1: Return one\n; -- Line 2: End statement\n```\n"
        "```text\nOutput\n| value |\n| 1 |\n```\n"
        "```text\nLine-by-line explanation\nLine 1 selects one. Line 2 ends the query.\n```"
    )
    assert all(check_coding(answer, "sql").values())
    missing_comment = answer.replace("; -- Line 2: End statement", ";")
    assert not check_coding(missing_comment, "sql")["all_lines_commented"]
    missing_explanation = answer.replace("Line 2 ends the query.", "The query ends.")
    assert not check_coding(missing_explanation, "sql")["all_lines_explained"]
    assert not check_coding(answer.replace("Output", "Notes"), "sql")["four_parts"]
