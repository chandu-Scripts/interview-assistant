import asyncio
import json
import logging
import re
from typing import Protocol

from interview_assistant.agents.rules import classify_rules, is_follow_up, looks_like_question
from interview_assistant.agents.specialists import SPECIALISTS
from interview_assistant.agents.types import Category, Decision
from interview_assistant.llm.router import RoutedResult
from interview_assistant.llm.types import LLMRequest, Message

log = logging.getLogger(__name__)

RULES_ACCEPT = 0.7  # rules at/above this confidence skip the LLM call

_CLASSIFY_SYSTEM = (
    "Classify the interview utterance. Reply with JSON only: "
    '{"category": "<one of: behavioral, hr, coding, system_design, data_sql, concept, general, '
    'not_a_question>", "confidence": <0..1>}. '
    "behavioral=past experience/STAR; hr=intro, motivation, salary; coding=write/analyse code or "
    "algorithms; system_design=architecture at scale; data_sql=SQL/data engineering; "
    "concept=explain a technical concept; general=other question; "
    "not_a_question=filler, acknowledgement or statement needing no answer."
)


class Completer(Protocol):
    async def __call__(self, request: LLMRequest, tier: str) -> RoutedResult: ...


def _tier_for(category: Category, text: str) -> str:
    spec = SPECIALISTS.get(category)
    tier = spec.tier if spec else "fast"
    if tier == "fast" and len(text.split()) > 60:  # long, multi-part questions deserve depth
        tier = "smart"
    return tier


def _decision(cat: Category, conf: float, source: str, text: str) -> Decision:
    agent = SPECIALISTS[cat].name if cat in SPECIALISTS else "none"
    return Decision(cat, _tier_for(cat, text), conf, source, agent)  # type: ignore[arg-type]


def _parse_llm(raw: str) -> tuple[Category, float] | None:
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
        return Category(str(obj["category"]).strip().lower()), float(obj.get("confidence", 0.6))
    except (ValueError, KeyError, TypeError):
        return None


class Classifier:
    """Rules first (microseconds); a small LLM call only when the rules are unsure."""

    def __init__(self, complete: Completer | None, timeout_s: float = 2.5) -> None:
        self._complete = complete
        self._timeout = timeout_s

    async def classify(
        self, text: str, context: str = "", prev: Category | None = None
    ) -> Decision:
        if not looks_like_question(text):
            return _decision(Category.NOT_A_QUESTION, 0.9, "rules", text)
        rule = classify_rules(text)
        if rule.confidence >= RULES_ACCEPT or self._complete is None:
            return _decision(rule.category, rule.confidence, "rules", text)
        if prev is not None and prev is not Category.NOT_A_QUESTION and is_follow_up(text):
            return _decision(prev, 0.7, "rules", text)  # follow-up: stay with the same specialist
        try:
            prompt = (
                f"{('Previous question: ' + context + chr(10)) if context else ''}Utterance: {text}"
            )
            req = LLMRequest(
                [Message("user", prompt)], system=_CLASSIFY_SYSTEM, max_tokens=60, temperature=0
            )
            res = await asyncio.wait_for(self._complete(req, "fast"), self._timeout)
            parsed = _parse_llm(res.text)
            if parsed is not None:
                cat, conf = parsed
                return _decision(cat, conf, "llm", text)
            log.warning("Unparseable classifier output; using rules")
        except Exception as e:  # timeout/provider failure: never block answering
            log.warning("LLM classification failed (%s); using rules", type(e).__name__)
        return _decision(rule.category, rule.confidence, "fallback", text)
