from dataclasses import dataclass
from typing import Literal

from interview_assistant.agents.types import Category

BASE = (
    "You are an interview copilot helping a candidate answer live interview questions. "
    "Your output is read at a glance while the candidate is speaking, so be direct: lead with the "
    "answer, use short sentences and tight bullets, no preamble, no restating the question, no "
    "meta-commentary. Never invent employers, projects, dates, team sizes or metrics (percentages, "
    "dollar amounts, counts) that are not given in the candidate profile or the question. If a "
    "specific number or name would make the answer stronger but none is given, use a clearly "
    "marked placeholder such as [metric], [Company] or [team size] instead of making one up. "
    "Answer the latest user question. Previous exchanges are context for follow-ups, not a "
    "request to repeat an earlier answer. For technical definitions or comparisons, explain "
    "the requested concepts directly; do not substitute a self-introduction or resume summary. "
    "Use candidate background only when relevant to the question."
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
        "Answer in first person using STAR (Situation, Task, Action, Result), labelled and brief. "
        "Emphasise the candidate's own actions and a measurable result. About 150-200 words.",
    ),
    Category.HR: Specialist(
        "hr",
        Category.HR,
        "fast",
        450,
        "Give a confident, concise first-person answer (about 100-150 words) that is honest and "
        "positive, tied to the candidate's profile. For salary, give a range and stay flexible.",
    ),
    Category.CODING: Specialist(
        "coding",
        Category.CODING,
        "smart",
        1200,
        "Give: 1) the approach in 2-3 bullets, 2) clean code in one fenced block with the "
        "language the question implies (default Python), 3) time and space complexity, "
        "4) one edge case. Keep explanations short.",
    ),
    Category.SYSTEM_DESIGN: Specialist(
        "system_design",
        Category.SYSTEM_DESIGN,
        "smart",
        1200,
        "Structure: requirements and assumptions, high-level components, data model/APIs, scaling "
        "and bottlenecks, trade-offs. Use compact bullets; numbers only as rough estimates.",
    ),
    Category.DATA_SQL: Specialist(
        "data_sql",
        Category.DATA_SQL,
        "smart",
        1000,
        "Show the SQL (or data approach) in a fenced block first. Then, briefly: 1) one plain-"
        "English sentence saying what it does, 2) how ties/duplicates are handled and what you "
        "get if no row matches (NULL vs empty), 3) one alternative when relevant (e.g. a window "
        "function like RANK/DENSE_RANK/ROW_NUMBER), 4) one performance note (an index or join "
        "order). State schema assumptions (table/column names) up front if not given.",
    ),
    Category.CONCEPT: Specialist(
        "concept",
        Category.CONCEPT,
        "fast",
        500,
        "Definition in one sentence, then how it works in 2-4 bullets, then one practical example "
        "or trade-off. Under 150 words.",
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
) -> str:
    """The extra keyword-only args come from an active "Create Interview Session" (Step 17);
    every one of them defaults to empty, so a plain call behaves exactly as it always did."""
    parts = [BASE, spec.prompt]
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
    if prefs:
        parts.append(f"Answer preferences for this session - {prefs}.")
    if language.strip() and language.strip().lower() != "english":
        parts.append(f"Answer in {language.strip()}.")
    if instructions:
        parts.append(
            "Session instructions from the candidate. They take priority over the style, length, "
            "formatting and tone rules above, and you must follow them exactly. They never "
            "override the rules against inventing facts or replacing the latest question with "
            "an unrelated answer:\n" + instructions.strip()
        )
    return "\n\n".join(parts)
