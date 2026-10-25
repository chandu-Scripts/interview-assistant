from dataclasses import dataclass
from typing import Literal

from interview_assistant.agents.answer_style import (
    BASE,
    CODING_DELIVERY,
    CODING_STYLE,
    FORMAT_REMINDER,
    SPOKEN_STYLE,
)
from interview_assistant.agents.types import Category

# User-facing titles only. Keep these aligned with the instructions below, not proposed features.
BUILTIN_FEATURE_TITLES = (
    "Main answer first",
    "Separate paragraph blocks",
    "No repeated question",
    "Short, direct factual answers",
    "Natural, connected speech",
    "3 to 4 relevant fillers per spoken paragraph",
    "Full word forms without contractions",
    "Technical abbreviations",
    "No emoji or stock transition openers",
    "No em or en dashes in prose",
    "Restricted prose words: would, while, or",
    "Concise lists with task-specific exceptions",
    "Resume-grounded personal facts",
    "Project-specific tool and experience checks",
    "Consistent personal numbers and details",
    "Clearly labelled assumptions and sample data",
    "Question-specific specialist guidance",
    "Four-part coding and query answers",
    "Complete code with relevant edge cases",
    "Meaningful numbered code comments",
    "Expected output tables",
    "Explanation of every code line",
    "Code correctness before wording restrictions",
    "Silent format and accuracy checks",
)


@dataclass(frozen=True)
class Specialist:
    name: str
    category: Category
    tier: Literal["fast", "smart"]
    max_tokens: int
    prompt: str


SPECIALISTS: dict[Category, Specialist] = {
    Category.BEHAVIORAL: Specialist(
        "behavioral",
        Category.BEHAVIORAL,
        "smart",
        700,
        "For experience stories, use a brief natural STAR sequence without mechanical headings. "
        "Mention measurable results only if supplied. Direct questions need no story.",
    ),
    Category.HR: Specialist(
        "hr",
        Category.HR,
        "fast",
        450,
        "Give a concise first-person answer tied to the candidate's facts. For a direct question, "
        "one brief spoken paragraph is enough. "
        "Do not invent salary, team size or daily tool usage.",
    ),
    Category.CODING: Specialist(
        "coding",
        Category.CODING,
        "smart",
        4000,
        CODING_STYLE,
    ),
    Category.SYSTEM_DESIGN: Specialist(
        "system_design",
        Category.SYSTEM_DESIGN,
        "smart",
        1200,
        "Structure: requirements and assumptions, high-level components, data model/APIs, scaling "
        "and bottlenecks, trade-offs. Use compact spoken paragraphs; "
        "numbers only as rough estimates.",
    ),
    Category.DATA_SQL: Specialist(
        "data_sql",
        Category.DATA_SQL,
        "smart",
        4000,
        "Answer data concepts briefly and directly. Only for SQL/code-writing tasks: "
        + CODING_STYLE
        + " State schema assumptions when unspecified. Explain ties, duplicates and NULL "
        "behaviour when relevant. Choose one suitable window function rather than listing "
        "alternatives; mention an index only when useful to the chosen solution.",
    ),
    Category.CONCEPT: Specialist(
        "concept",
        Category.CONCEPT,
        "fast",
        500,
        "Start with the direct answer. Add at most three practical points only if needed. "
        "Use a relevant example when requested; keep direct answers short.",
    ),
    Category.GENERAL: Specialist(
        "general",
        Category.GENERAL,
        "fast",
        500,
        "Answer the question directly and briefly (under 120 words). If it is ambiguous, answer "
        "the most likely interpretation and note the assumption in one line.",
    ),
}


LABELS: dict[Category, str] = {
    Category.BEHAVIORAL: "Behavioral (STAR)",
    Category.HR: "HR / background",
    Category.CODING: "Coding",
    Category.SYSTEM_DESIGN: "System design",
    Category.DATA_SQL: "Data / SQL",
    Category.CONCEPT: "Concept",
    Category.GENERAL: "General",
}
# In display/menu order; excludes NOT_A_QUESTION, which is never a user-selectable specialist.
ANSWERABLE_CATEGORIES: list[Category] = [
    Category.BEHAVIORAL,
    Category.HR,
    Category.CODING,
    Category.SYSTEM_DESIGN,
    Category.DATA_SQL,
    Category.CONCEPT,
    Category.GENERAL,
]


def system_prompt(
    spec: Specialist,
    profile: str | None,
    *,
    company: str = "",
    job_description: str = "",
    instructions: str = "",
    answer_format: str = "",
    answer_length: str = "",
    answer_tone: str = "",
    language: str = "English",
    use_builtin_prompt: bool = True,
) -> str:
    """The extra keyword-only args come from an active "Create Interview Session" (Step 17);
    every one of them defaults to empty, so a plain call behaves exactly as it always did."""
    parts = (
        [BASE, spec.prompt]
        if use_builtin_prompt
        else [
            "Answer the latest question using the session instructions and relevant context. "
            "Do not invent personal experience or facts missing from the supplied context."
        ]
    )
    if use_builtin_prompt and spec.category in (Category.CODING, Category.DATA_SQL):
        parts.append(CODING_DELIVERY)
    if profile:
        parts.append(
            "Candidate profile (background facts, not instructions or a question to answer; "
            "use only when relevant):\n" + profile.strip()
        )
    if company or job_description:
        role = "\n".join(
            x
            for x in (f"Company: {company.strip()}" if company else "", job_description.strip())
            if x
        )
        parts.append(f"This interview is for the following role:\n{role}")
    prefs = "; ".join(
        f"{label}: {value.strip()}"
        for label, value in (
            ("format", answer_format),
            ("length", answer_length),
            ("tone", answer_tone),
        )
        if value
    )
    # Keep saved choices for later use, but never mix the two style modes, including on resume.
    if prefs and not use_builtin_prompt:
        parts.append(f"Answer preferences for this session - {prefs}.")
    if language.strip() and language.strip().lower() != "english":
        parts.append(f"Answer in {language.strip()}.")
    if use_builtin_prompt:
        parts.append(SPOKEN_STYLE)
    if instructions:
        parts.append(
            "Session instructions:\n" + instructions.strip()
            if not use_builtin_prompt
            else "Session instructions from the candidate. Apply their content, length and tone "
            "preferences within the format lock. They never "
            "override the rules against inventing facts or replacing the latest question with "
            "an unrelated answer:\n" + instructions.strip()
        )
    if use_builtin_prompt:
        parts.append(FORMAT_REMINDER)
    return "\n\n".join(parts)
