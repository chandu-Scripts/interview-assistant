"""Best-effort nudge: flag numeric-sounding claims an answer states that were not in the candidate
profile or the question, so the user can check them before repeating them out loud.

This is NOT a fact-checker: it only catches the "the model stated a number nobody gave it" case,
which is the common failure mode for behavioral/HR answers (invented percentages, dollar amounts).
It never edits the answer text.
"""

import re

# Percentages ("20%"), money ("$50k", "£1.2m"), and "N years/months" duration claims.
_PERCENT = re.compile(r"\b\d{1,3}(?:\.\d+)?\s?%")
_MONEY = re.compile(r"[$£€]\s?\d[\d,]*(?:\.\d+)?\s?(?:k|K|m|M|million|billion)?\b")

# Categories where a fabricated metric is the realistic failure mode. Technical categories
# (coding/system design/SQL/concept) legitimately use numbers (complexity, ports, LIMIT n) that
# are not "claims" about the candidate's own experience, so they are not checked.
CLAIM_PRONE = frozenset({"behavioral", "hr", "general"})


def find_unverified_claims(answer: str, context: str) -> list[str]:
    """Numeric claims in `answer` whose digits do not appear anywhere in `context`
    (profile + question). Returns the matched snippets in order, each at most once."""
    if not answer or not answer.strip():
        return []
    context_low = context.lower()
    found: list[str] = []
    for pattern in (_PERCENT, _MONEY):
        for m in pattern.finditer(answer):
            raw = m.group(0).strip()
            digits = re.sub(r"\D", "", raw)
            if not digits or digits in context_low or raw.lower() in context_low:
                continue
            if raw not in found:
                found.append(raw)
    return found
