"""Project-overview routing without stealing narrow project questions."""

import pytest

from interview_assistant.agents.classifier import Classifier
from interview_assistant.agents.jev import Jev, JevConfig
from interview_assistant.agents.types import Category
from interview_assistant.config.settings import Settings
from interview_assistant.llm.router_factory import build_router
from interview_assistant.llm.types import StreamEvent


@pytest.mark.parametrize(
    "question",
    [
        "Explain your current project.",
        "Tell me about your last project",
        "Describe your project",
        "Can you walk me through your current project?",
        "Could you please explain your recent data engineering project in detail?",
        "So, tell us about your latest ETL project",
        "Give me an overview of your current project",
        "Walk us through your main project end to end, please.",
        "What's your current project about?",
        "what is your recent project",
    ],
)
async def test_overviews_use_experience_specialist_without_llm(question):
    async def unexpected_llm(*args):
        pytest.fail("Clear project overview must not add a classification API call")

    decision = await Classifier(unexpected_llm).classify(question)
    assert decision.category is Category.BEHAVIORAL
    assert decision.source == "rules" and decision.confidence >= 0.7


@pytest.mark.parametrize(
    "question, expected",
    [
        ("How many catalogs did you use in your project?", Category.GENERAL),
        ("Explain your project architecture.", Category.SYSTEM_DESIGN),
        ("Explain your project SQL query.", Category.DATA_SQL),
        ("Write a Python function for your project.", Category.CODING),
        ("Write a function to explain your current project", Category.CODING),
        ("Explain how Spark works in your project.", Category.CONCEPT),
        ("Explain your project's database indexes.", Category.DATA_SQL),
        ("Tell me about yourself.", Category.HR),
        ("What is the difference between a list and a tuple?", Category.CONCEPT),
        ("How do you find the second highest salary in the project database?", Category.DATA_SQL),
        ("Which project used Kafka?", Category.GENERAL),
        ("How many projects have you worked on?", Category.GENERAL),
    ],
)
async def test_narrow_questions_keep_existing_routes(question, expected):
    assert (await Classifier(None).classify(question)).category is expected


@pytest.mark.parametrize("builtin", [True, False])
async def test_overview_request_uses_supported_context_fast_selection_and_existing_budget(builtin):
    requests = []

    class Provider:
        name = "openai"

        async def stream(self, request):
            requests.append(request)
            yield StreamEvent(delta="Synthetic project answer.", finish_reason="stop")

    jev = Jev(
        build_router(Settings(_env_file=None), {"openai": Provider()}),
        JevConfig(
            classify_with_llm=False,
            use_builtin_prompt=builtin,
            profile="Synthetic project: daily ADF ingestion into ADLS Gen2.",
            instructions="Use short sentences.",
            fast_model_route="openai:gpt-6.1-terra",
            coding_model_route="openai:gpt-6-astra",
        ),
    )
    _ = [event async for event in jev.ask("Explain your current project")]
    assert len(requests) == 1
    request = requests[0]
    assert request.model == "gpt-6.1-terra" and request.max_tokens == 700
    assert "daily ADF ingestion" in request.system
    assert "Use short sentences." in request.system
    assert ("For a project overview" in request.system) is builtin
    assert ("personal responsibility" in request.system) is builtin
    assert "3 to 4" not in request.system


async def test_specific_incident_and_followup_still_use_experience_route():
    classifier = Classifier(None)
    question = "Tell me about a time you resolved a conflict on your project."
    assert (await classifier.classify(question)).category is Category.BEHAVIORAL
    followup = await classifier.classify(
        "What was the outcome of that?", question, Category.BEHAVIORAL
    )
    assert followup.category is Category.BEHAVIORAL
