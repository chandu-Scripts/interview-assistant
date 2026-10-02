from dataclasses import dataclass
from enum import StrEnum
from typing import Literal


class Category(StrEnum):
    BEHAVIORAL = "behavioral"  # STAR-style "tell me about a time..."
    HR = "hr"  # intro, motivation, salary, notice period
    CODING = "coding"
    SYSTEM_DESIGN = "system_design"
    DATA_SQL = "data_sql"
    CONCEPT = "concept"  # "what is / difference between / how does X work"
    GENERAL = "general"  # anything else that is still a question
    NOT_A_QUESTION = "not_a_question"  # filler, acknowledgements, statements


@dataclass(frozen=True)
class Decision:
    """Jev's routing decision for one utterance."""

    category: Category
    tier: Literal["fast", "smart"]
    confidence: float
    source: Literal["rules", "llm", "fallback", "user"]  # "user" = a manual correction
    agent: str

    @property
    def should_answer(self) -> bool:
        return self.category is not Category.NOT_A_QUESTION


@dataclass(frozen=True)
class Turn:
    question: str
    answer: str
    category: Category
