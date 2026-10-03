import asyncio

import pytest

from interview_assistant.agents.classifier import Classifier, _parse_llm
from interview_assistant.agents.jev import Jev, JevConfig
from interview_assistant.agents.rules import classify_rules, is_follow_up, looks_like_question
from interview_assistant.agents.specialists import SPECIALISTS, system_prompt
from interview_assistant.agents.types import Category as C
from interview_assistant.agents.types import Decision
from interview_assistant.llm.router import Route, RoutedResult, Router, RouterConfig
from interview_assistant.llm.types import ErrorKind, LLMError, LLMRequest, Usage
from tests.test_router import Script

# (utterance, expected category) - the rule classifier must get these right on its own.
CORPUS = [
    ("Tell me about a time you resolved a conflict on your team.", C.BEHAVIORAL),
    ("Describe a situation where you missed a deadline.", C.BEHAVIORAL),
    ("What's your greatest weakness?", C.HR),
    ("Give me an example of a time you showed leadership.", C.BEHAVIORAL),
    ("What was your biggest failure and what did you learn?", C.BEHAVIORAL),
    ("Tell me about yourself.", C.HR),
    ("Why do you want to work here?", C.HR),
    ("What are your salary expectations?", C.HR),
    ("What is your notice period?", C.HR),
    ("Where do you see yourself in five years?", C.HR),
    ("Write a function to reverse a linked list.", C.CODING),
    ("Implement an algorithm to check if a string is a palindrome.", C.CODING),
    ("How would you solve the two sum problem, and what is the time complexity?", C.CODING),
    ("What's the time complexity of binary search?", C.CODING),
    ("Can you implement a LRU cache in Python?", C.CODING),
    ("Design a URL shortener.", C.SYSTEM_DESIGN),
    ("Design a scalable chat system that handles millions of users.", C.SYSTEM_DESIGN),
    ("How would you scale this service and add a load balancer?", C.SYSTEM_DESIGN),
    ("Explain how you would shard a database for high availability.", C.SYSTEM_DESIGN),
    ("What is the difference between an inner join and a left join?", C.DATA_SQL),
    ("Write a SQL query to find the second highest salary.", C.DATA_SQL),
    ("How do window functions work in SQL?", C.DATA_SQL),
    ("Explain database normalization.", C.DATA_SQL),
    ("How do you design an ETL data pipeline in Spark?", C.DATA_SQL),
    ("What is the difference between a process and a thread?", C.CONCEPT),
    ("Explain how garbage collection works.", C.CONCEPT),
    ("What are the pros and cons of microservices versus a monolith?", C.CONCEPT),
    ("What is polymorphism?", C.CONCEPT),
    # Regression: the word "salary" alone used to force HR (90% confident) on SQL questions.
    ("How do you find the second highest salary?", C.DATA_SQL),
    ("Find the second highest salary from the employees table.", C.DATA_SQL),
    ("How to find the 2nd highest salary in SQL?", C.DATA_SQL),
    ("Write a query to get the second highest salary from employees.", C.DATA_SQL),
    ("Find the nth highest salary in each department.", C.DATA_SQL),
    ("Write a query to find the highest salary in each department.", C.DATA_SQL),
    # Speech-to-text often writes "SQL" as "sequel".
    ("How would you write a sequel query to remove duplicate rows?", C.DATA_SQL),
    # ...while genuine salary/HR questions must still go to HR.
    ("What is your current salary?", C.HR),
    ("What is your expected CTC?", C.HR),
    ("How much do you expect to earn?", C.HR),
    ("What are your expectations from this role?", C.HR),
]

FILLER = ["Okay.", "Great, thanks.", "Hmm", "Got it", "Yeah sure.", "Alright", "Thank you", ""]


@pytest.mark.parametrize(("text", "expected"), CORPUS)
def test_rules_corpus(text, expected):
    r = classify_rules(text)
    assert r.category is expected, (text, r)


def test_rules_confident_on_clear_cases():
    assert (
        classify_rules("Tell me about a time you resolved a conflict on your team.").confidence
        >= 0.7
    )
    assert classify_rules("Write a SQL query to find duplicates.").confidence >= 0.7
    assert classify_rules("Hmm interesting thing about weather").confidence < 0.7


@pytest.mark.parametrize("text", FILLER)
def test_filler_is_not_a_question(text):
    assert not looks_like_question(text)


@pytest.mark.parametrize(
    "text",
    [
        "Why did you choose Postgres?",
        "Tell me about your last project",
        "Can you walk me through your approach",
        "So, how does caching work here",
    ],
)
def test_questions_detected(text):
    assert looks_like_question(text)


def test_statement_without_cue_is_not_question():
    assert not looks_like_question("I worked at a bank for many years")


# ---------------- classifier ----------------
class FakeCompleter:
    def __init__(self, text="", exc=None, delay=0.0):
        self.text, self.exc, self.delay, self.calls = text, exc, delay, 0

    async def __call__(self, request: LLMRequest, tier: str) -> RoutedResult:
        self.calls += 1
        await asyncio.sleep(self.delay)
        if self.exc:
            raise self.exc
        return RoutedResult(text=self.text, usage=Usage(1, 1))


async def test_confident_rules_skip_llm():
    comp = FakeCompleter('{"category": "coding", "confidence": 1}')
    d = await Classifier(comp).classify(
        "Tell me about a time you resolved a conflict on your team."
    )
    assert d.category is C.BEHAVIORAL and d.source == "rules" and comp.calls == 0
    assert d.agent == "behavioral" and d.tier == "smart"


async def test_unsure_rules_use_llm():
    comp = FakeCompleter('Sure! {"category": "system_design", "confidence": 0.8}')
    d = await Classifier(comp).classify("How would you handle a million writes per second?")
    assert d.category is C.SYSTEM_DESIGN and d.source == "llm" and comp.calls == 1
    assert d.confidence == 0.8


@pytest.mark.parametrize(
    "comp",
    [
        FakeCompleter(exc=LLMError("x", ErrorKind.SERVER, "down")),
        FakeCompleter("no json here"),
        FakeCompleter('{"category": "bogus"}'),
        FakeCompleter('{"category":"coding"}', delay=1.0),  # exceeds timeout
    ],
)
async def test_llm_failure_falls_back_to_rules(comp):
    d = await Classifier(comp, timeout_s=0.1).classify(
        "Why did you choose Postgres over MySQL here?"
    )
    assert d.source == "fallback" and d.should_answer


async def test_no_completer_and_filler():
    c = Classifier(None)
    assert (await c.classify("How would you approach this?")).source == "rules"
    d = await c.classify("Okay, thanks.")
    assert d.category is C.NOT_A_QUESTION and not d.should_answer and d.agent == "none"


async def test_follow_up_inherits_previous_category_without_llm():
    comp = FakeCompleter('{"category": "hr", "confidence": 1}')
    c = Classifier(comp)
    d = await c.classify("What was the outcome of that?", "prev q", C.CODING)
    assert d.category is C.CODING and d.source == "rules" and comp.calls == 0
    # no previous turn -> normal path (LLM), and a long unrelated question is not a follow-up
    d2 = await c.classify("What was the outcome of that?")
    assert d2.source == "llm" and comp.calls == 1
    assert not is_follow_up("Design a system that stores every user event and replays it later")
    assert not is_follow_up("Explain how that works")
    assert is_follow_up("Why did that happen?")


def test_parse_llm():
    assert _parse_llm('{"category":"HR","confidence":0.9}') == (C.HR, 0.9)
    assert _parse_llm("nothing") is None


def test_long_question_upgrades_tier():
    from interview_assistant.agents.classifier import _tier_for

    assert _tier_for(C.CONCEPT, "what is x") == "fast"
    assert _tier_for(C.CONCEPT, "word " * 70) == "smart"


# ---------------- specialists ----------------
def test_every_answerable_category_has_a_specialist():
    for cat in C:
        if cat is not C.NOT_A_QUESTION:
            assert cat in SPECIALISTS and SPECIALISTS[cat].max_tokens > 0


def test_system_prompt_includes_profile():
    p = system_prompt(SPECIALISTS[C.CODING], "5 yrs Python at Acme")
    assert "5 yrs Python at Acme" in p and "complexity" in p
    assert "Candidate profile" not in system_prompt(SPECIALISTS[C.CODING], None)


def test_base_prompt_forbids_invented_metrics():
    p = system_prompt(SPECIALISTS[C.BEHAVIORAL], None)
    assert "percentages" in p and "placeholder" in p and "[metric]" in p


def test_data_sql_prompt_covers_ties_null_alternative_and_index():
    p = SPECIALISTS[C.DATA_SQL].prompt
    assert "tie" in p.lower() and "null" in p.lower()
    assert "window function" in p.lower() or "rank" in p.lower()
    assert "index" in p.lower() and "schema" in p.lower()


# ---------------- specialists: Step 17 session context (company/JD/instructions/preferences) ----
def test_system_prompt_with_no_session_context_is_unchanged():
    """Every new kwarg defaults to empty, so an old-style call behaves exactly as it always did."""
    p = system_prompt(SPECIALISTS[C.CODING], "5 yrs Python")
    assert "This interview is for" not in p
    assert "Answer preferences for this session" not in p
    assert "Additional instructions" not in p


def test_system_prompt_includes_company_and_job_description():
    p = system_prompt(
        SPECIALISTS[C.CODING], None, company="Google", job_description="Build search infra."
    )
    assert "This interview is for the following role" in p
    assert "Company: Google" in p and "Build search infra." in p


def test_system_prompt_job_description_alone_is_included_without_a_company_line():
    p = system_prompt(SPECIALISTS[C.CODING], None, job_description="Build search infra.")
    assert "Company:" not in p and "Build search infra." in p


def test_system_prompt_includes_answer_preferences():
    p = system_prompt(
        SPECIALISTS[C.CODING],
        None,
        answer_format="Bullet points",
        answer_length="Short",
        answer_tone="Formal",
    )
    assert "Answer preferences for this session" in p
    assert "format: Bullet points" in p and "length: Short" in p and "tone: Formal" in p


def test_system_prompt_partial_answer_preferences_omit_the_others():
    p = system_prompt(SPECIALISTS[C.CODING], None, answer_tone="Casual")
    assert "tone: Casual" in p and "format:" not in p and "length:" not in p


def test_system_prompt_includes_ai_instructions():
    p = system_prompt(SPECIALISTS[C.CODING], None, instructions="Always mention trade-offs.")
    assert "Additional instructions from the candidate" in p
    assert "Always mention trade-offs." in p


def test_manual_decision_builds_a_confident_user_sourced_decision():
    jev, _ = make_jev(["ok"])
    d = jev.manual_decision(C.DATA_SQL)
    assert d.category is C.DATA_SQL and d.source == "user" and d.confidence == 1.0
    assert d.tier == SPECIALISTS[C.DATA_SQL].tier and d.agent == SPECIALISTS[C.DATA_SQL].name


async def test_manual_decision_can_be_used_to_ask_directly():
    jev, _ = make_jev(["fixed answer"])
    d = jev.manual_decision(C.DATA_SQL)
    events = [e async for e in jev.ask("Find the second highest salary?", d)]
    assert isinstance(events[0], Decision) and events[0] is d
    assert "".join(e.event.delta for e in events[1:]) == "fixed answer"
    assert jev.history[-1].category is C.DATA_SQL


# ---------------- Jev end to end (scripted providers) ----------------
def make_jev(steps_a, steps_b=None, **cfg):
    a = Script("a", steps_a)
    provs = {"a": a}
    chain = [Route("a", "am")]
    if steps_b is not None:
        provs["b"] = Script("b", steps_b)
        chain.append(Route("b", "bm"))
    router = Router(provs, RouterConfig(chains={"fast": chain, "smart": chain}))
    return Jev(router, JevConfig(**cfg)), provs


async def collect(jev, q):
    return [e async for e in jev.ask(q)]


async def test_jev_routes_and_streams():
    jev, provs = make_jev(["Use ", "a hash map."])
    events = await collect(jev, "Write a function to solve the two sum problem.")
    assert isinstance(events[0], Decision) and events[0].category is C.CODING
    assert "".join(e.event.delta for e in events[1:]) == "Use a hash map."
    assert provs["a"].calls == ["am"]
    assert jev.history[-1].answer == "Use a hash map." and jev.history[-1].category is C.CODING


async def test_jev_ignores_non_questions():
    jev, provs = make_jev(["x"])
    events = await collect(jev, "Okay, great.")
    assert len(events) == 1 and not events[0].should_answer
    assert provs["a"].calls == [] and jev.history == []


async def test_jev_sends_specialist_prompt_and_history():
    seen = []

    class Spy(Script):
        async def stream(self, request):
            seen.append(request)
            async for e in super().stream(request):
                yield e

    a = Spy("a", ["answer"])
    router = Router(
        {"a": a}, RouterConfig(chains={"fast": [Route("a", "m")], "smart": [Route("a", "m")]})
    )
    jev = Jev(router, JevConfig(profile="Backend dev, 5 yrs"))
    await collect(jev, "Tell me about a time you resolved a conflict on your team.")
    await collect(jev, "What was the outcome of that conflict with your team?")
    first, second = seen
    assert "STAR" in first.system and "Backend dev, 5 yrs" in first.system
    assert first.max_tokens == SPECIALISTS[C.BEHAVIORAL].max_tokens
    assert [m.role for m in second.messages] == ["user", "assistant", "user"]
    assert second.messages[0].content.startswith("Tell me about a time")


async def test_history_is_capped():
    jev, _ = make_jev(["ok"], history_turns=2, history_chars=10_000)
    for i in range(6):
        await collect(jev, f"Explain how thing number {i} works?")
    msgs = jev._messages("next?")
    assert len(msgs) == 2 * 2 + 1
    assert len(jev.history) <= 10


async def test_history_char_budget():
    jev, _ = make_jev(["x" * 500], history_turns=3, history_chars=600)
    for i in range(3):
        await collect(jev, f"Explain how thing {i} works?")
    assert len(jev._messages("q?")) == 3  # only one previous turn fits the budget


async def test_failover_reset_is_not_stored_twice():
    jev, _ = make_jev(["par", "tial", LLMError("a", ErrorKind.SERVER, "x")], ["clean answer"])
    events = await collect(jev, "Explain how garbage collection works?")
    assert any(getattr(e, "reset", False) for e in events)
    assert jev.history[-1].answer == "clean answer"


async def test_interrupted_answer_not_remembered():
    jev, _ = make_jev(["start", ("sleep", 10)])

    async def consume():
        async for _ in jev.ask("Explain how garbage collection works?"):
            pass

    t = asyncio.create_task(consume())
    await asyncio.sleep(0.1)
    t.cancel()
    with pytest.raises(asyncio.CancelledError):
        await t
    assert jev.history == []


async def test_clear_history():
    jev, _ = make_jev(["ok"])
    await collect(jev, "Explain how garbage collection works?")
    jev.clear_history()
    assert jev.history == []
