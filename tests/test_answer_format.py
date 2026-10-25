import pytest

from interview_assistant.agents.jev import Jev, JevConfig
from interview_assistant.agents.specialists import (
    CODING_DELIVERY,
    FORMAT_REMINDER,
    SPECIALISTS,
    SPOKEN_STYLE,
    system_prompt,
)
from interview_assistant.agents.types import Category
from interview_assistant.app.answer_format import format_answer_blocks
from interview_assistant.config.settings import Settings
from interview_assistant.llm.router_factory import build_router
from interview_assistant.ui.answer_view import split_blocks
from tests.test_router import Script


@pytest.mark.parametrize(
    "raw",
    [
        "First paragraph.\n\nSecond paragraph.",
        "```python\nx = 1  # Line 1\nprint(x)  # Line 2",
        "Approach\n\n```python\nx = '''a\n\nb'''\n```\n\nExpected output\n\nx",
        "~~~sql\nSELECT 1; -- Line 1\n~~~",
    ],
)
def test_format_closes_blocks_and_is_idempotent(raw):
    result = format_answer_blocks(raw)
    assert result.startswith("```") and result.endswith("```")
    assert format_answer_blocks(result) == result


def test_format_preserves_code_whitespace_and_words():
    code = "def f():  # Line 1\n    return 'or while would'  # Line 2"
    result = format_answer_blocks(f"Explanation\n\n```python\n{code}\n```")
    assert code in result
    assert result.startswith("```text\nExplanation\n```")
    assert format_answer_blocks("") == ""


def test_prompt_style_for_all_specialists():
    for spec in SPECIALISTS.values():
        prompt = system_prompt(spec, "Project A: ADF. Skills: Databricks.")
        assert prompt.startswith("FORMAT LOCK:")
        assert prompt.endswith("with nothing outside.")
        assert "SAME project" in prompt and "skills list alone" in prompt
        assert "Check 0" in prompt and "Do not repeat the question" in prompt
        assert "must not be assumed" in prompt
    for category in (Category.CODING, Category.DATA_SQL):
        prompt = system_prompt(SPECIALISTS[category], None)
        assert "approach, executable code, output table" in prompt
        assert "Line N" in prompt and "Do not claim the code was run" in prompt


@pytest.mark.parametrize("category", [Category.CODING, Category.DATA_SQL])
def test_coding_delivery_wins_over_general_brevity_and_preserves_toggle(category):
    kwargs = {"answer_length": "Short", "instructions": "Use readable names."}
    prompt = system_prompt(SPECIALISTS[category], None, **kwargs)
    assert prompt.endswith(FORMAT_REMINDER)
    assert prompt.count(CODING_DELIVERY) == 1
    assert prompt.count(SPOKEN_STYLE) == 1
    assert "Line N: explanation" in prompt
    assert "cover EVERY" in prompt
    assert "time and space complexity" in prompt
    assert "edge-case handling" in prompt
    assert "not executed" in prompt
    assert "For concept questions and follow-ups" in prompt
    disabled = system_prompt(SPECIALISTS[category], None, use_builtin_prompt=False, **kwargs)
    assert CODING_DELIVERY not in disabled
    assert "Line N" not in disabled
    assert "Use readable names." in disabled


@pytest.mark.parametrize(
    "question, category",
    [
        ("Write a Python function to merge overlapping intervals.", Category.CODING),
        ("Write a SQL query for the top two distinct salaries per department.", Category.DATA_SQL),
    ],
)
async def test_four_part_prompt_reaches_provider_without_extra_round_trip(question, category):
    requests = []

    class Capture(Script):
        async def stream(self, request):
            requests.append(request)
            async for event in super().stream(request):
                yield event

    provider = Capture("openai", ["Answer"])
    jev = Jev(
        build_router(Settings(_env_file=None), {"openai": provider}),
        JevConfig(classify_with_llm=False, answer_length="Short"),
    )
    events = [event async for event in jev.ask(question)]
    assert events[0].category is category
    assert len(requests) == 1
    assert CODING_DELIVERY in requests[0].system
    assert requests[0].system.endswith(FORMAT_REMINDER)
    assert requests[0].max_tokens == 4000


def test_four_parts_and_inline_comments_survive_answer_rendering(qapp):
    code = (
        "SELECT department, MAX(salary) AS salary -- Line 1: Select each department's maximum\n"
        "FROM employees -- Line 2: Read the employee records\n"
        "GROUP BY department; -- Line 3: Aggregate within each department"
    )
    raw = (
        "```text\nApproach\nGroup employees by department.\n```\n"
        f"```sql\n{code}\n```\n"
        "```text\nOutput (illustrative, expected, not executed)\n"
        "department | salary\nA | 100\n```\n"
        "```text\nLine-by-line explanation\n"
        "Line 1: Return the grouping key and the highest salary.\n"
        "Line 2: Read the source table.\n"
        "Line 3: Make one result row per department.\n```"
    )
    blocks = split_blocks(format_answer_blocks(raw), True)
    assert len(blocks) == 4
    assert blocks[0].text.startswith("Approach")
    assert blocks[1].code and blocks[1].text == code
    assert blocks[2].text.startswith("Output")
    assert blocks[3].text.startswith("Line-by-line explanation")
    for number in range(1, 4):
        assert f"Line {number}:" in blocks[3].text


@pytest.mark.parametrize("spec", SPECIALISTS.values())
def test_one_style_policy_with_extra_guidance_after_defaults(spec):
    extra = "Use a shopping list analogy."
    prompt = system_prompt(spec, None, instructions=extra)
    assert prompt.count("FILLER RULE:") == 1
    assert prompt.index(SPOKEN_STYLE) < prompt.index(extra) < prompt.index(FORMAT_REMINDER)
    assert "em dashes (U+2014)" in prompt and "en dashes (U+2013)" in prompt
    assert "1 or 2" not in prompt and "one sentence may be enough" not in prompt
    if spec.category in (Category.CODING, Category.DATA_SQL):
        assert "Group 2 to 3 consecutive line explanations" in prompt
        assert "General optimisation questions do not require sample code" in prompt
    disabled = system_prompt(spec, None, instructions=extra, use_builtin_prompt=False)
    assert extra in disabled and "FILLER RULE" not in disabled
