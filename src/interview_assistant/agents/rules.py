import re
from dataclasses import dataclass

from interview_assistant.agents.types import Category

# (pattern, weight). Weight 3 = strong signal, 1 = weak hint.
_PATTERNS: dict[Category, list[tuple[str, int]]] = {
    Category.DATA_SQL: [
        (r"\b(sql|sequel|t-sql|pl/sql)\b", 3),  # speech-to-text often writes "SQL" as "sequel"
        # The classic interview question: nth highest/lowest salary (or value) - always SQL.
        (
            r"\b(second|2nd|third|3rd|nth|\d+(st|nd|rd|th)|kth)\s+(highest|lowest|largest|smallest)\b",
            3,
        ),
        (r"\b(highest|lowest|average|total|maximum|minimum)\s+(salary|salaries|paid)\b", 3),
        (r"\b(employees?|orders?|customers?|department|products?|sales)\s+table\b", 2),
        (r"\b(duplicates?|rows?|columns?|records?|null values?)\b", 1),
        (r"\b(inner|left|right|outer|cross|self)\s+join\b", 3),
        (r"\b(group by|window function|cte|stored procedure|primary key|foreign key)\b", 3),
        (r"\b(query|queries|database|table|index(es)?|normali[sz]ation|postgres\w*|mysql)\b", 1),
        (r"\b(etl|data (warehouse|pipeline|model(l)?ing)|spark|pandas|dataframe)\b", 2),
    ],
    Category.SYSTEM_DESIGN: [
        (r"\bdesign (a|an|the)\b.*\b(system|service|platform|app|application|api)\b", 3),
        (r"\bdesign (a|an)\s+\w+(\s+\w+)?\s+(shortener|feed|chat|cache|queue|limiter)", 3),
        (r"\b(system design|architecture|scalab\w+|high availability|load balanc\w+)\b", 3),
        (r"\b(shard\w*|microservices?|throughput|cdn|replication|consistency|caching)\b", 2),
        (r"\b(latency|distributed|fault[- ]toleran\w+)\b", 1),
    ],
    Category.CODING: [
        (r"\b(write|implement|code|program)\b.*\b(function|method|class|algorithm|program)\b", 3),
        (r"\b(leetcode|two sum|linked list|binary (tree|search)|dynamic programming)\b", 3),
        (r"\b(time|space) complexity\b|\bbig[- ]o\b", 3),
        (r"\b(reverse|sort|array|string|recursion|hash ?map|stack|queue|graph|palindrome)\b", 2),
        (r"\b(python|java|javascript|c\+\+|golang|typescript)\b", 1),
    ],
    Category.BEHAVIORAL: [
        (
            r"\b(tell me about|describe|give me an example of|share) (a|an) (time|situation)\b",
            3,
        ),
        (r"\b(conflict|disagree\w*|difficult (coworker|colleague|teammate|stakeholder))\b", 2),
        (r"\b(failure|mistake|deadline|pressure|leadership|mentor\w*|feedback)\b", 2),
        (r"\b(greatest|biggest|proudest) (achievement|accomplishment|challenge)\b", 3),
        (r"\b(team|challenge|challenging|stakeholder|priorit\w+)\b", 1),
    ],
    Category.HR: [
        (r"\btell me about yourself\b|\bwalk me through your (resume|background)\b", 3),
        (r"\bwhy (do you want|should we hire|are you (leaving|looking))\b", 3),
        # "salary" alone is NOT an HR signal: "second highest salary" is a SQL question. Only the
        # personal / negotiation phrasings count.
        (
            r"\b(salary|pay|compensation|package|ctc)\s+(expectations?|range|requirements?|negotiation)\b"
            r"|\b(expected|desired|current|last|present)\s+(salary|compensation|ctc|package|pay)\b"
            r"|\byour\s+(current\s+|expected\s+)?(salary|compensation|ctc|package)\b"
            r"|\bhow much (do you|are you|would you) (earn|make|expect|want|looking)\b",
            3,
        ),
        (
            r"\b(notice period|relocat\w+|work(ing)? hours|start date|when can you (join|start))\b",
            3,
        ),
        (r"\byour expectations\b|\bexpectations (from|of) (this|the) (role|job|company|team)\b", 3),
        (
            r"\bwhere do you see yourself\b|\b(strengths?|weakness(es)?|why this (company|role))\b",
            3,
        ),
        (r"\b(your background|your experience|career goals?)\b", 1),
    ],
    Category.CONCEPT: [
        (r"\b(what is|what are|what's|whats)\b", 1),
        (r"\b(difference between|compare|pros and cons|advantages of)\b", 2),
        (r"\b(explain|how does|how do|why does)\b.*\b(work|works)\b", 2),
        (r"\b(explain|define|what do you mean by)\b", 1),
    ],
}

_QUESTION_CUES = re.compile(
    r"^\s*(so[, ]+|and[, ]+|okay[, ]+|ok[, ]+|now[, ]+)?"
    r"(tell|describe|explain|walk|what|whats|how|why|when|where|which|who|can|could|would|do|does|"
    r"did|have|has|are|is|give|design|write|implement|share|talk|compare|define|suppose|imagine)\b",
    re.I,
)
_ACK_ONLY = re.compile(
    r"^\s*(ok(ay)?|great|good|nice|thanks?( you)?|thank you|got it|right|sure|alright|"
    r"cool|perfect|awesome|yes|yeah|no|hmm+|uh+|um+|i see|makes sense|sounds good)[\s.,!]*$",
    re.I,
)


_FOLLOW_UP = re.compile(
    r"\b(that|it|this|those|them|there)\b|^\s*(and |so |but )?(why|how so|what happened|"
    r"what was the (outcome|result)|what did you learn|what would you do differently)\b",
    re.I,
)


_NEW_TASK = re.compile(r"^\s*(design|write|implement|explain|describe|tell|compare|define)\b", re.I)


def is_follow_up(text: str) -> bool:
    """Short question that leans on the previous exchange ("why did that fail?")."""
    if len(text.split()) > 8 or _NEW_TASK.match(text):
        return False
    return bool(_FOLLOW_UP.search(text))


@dataclass(frozen=True)
class RuleResult:
    category: Category
    confidence: float
    scores: dict[Category, int]


def looks_like_question(text: str) -> bool:
    t = text.strip()
    if not t or _ACK_ONLY.match(t):
        return False
    if "?" in t:
        return True
    return len(t.split()) >= 4 and bool(_QUESTION_CUES.match(t))


def classify_rules(text: str) -> RuleResult:
    """Fast keyword classifier. Confidence: 0.9 clear winner, 0.7 probable, 0.4 unsure."""
    t = text.lower()
    scores = {
        cat: sum(w for pat, w in pats if re.search(pat, t)) for cat, pats in _PATTERNS.items()
    }
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    (best, top), (_, second) = ranked[0], ranked[1]
    if top == 0:
        return RuleResult(Category.GENERAL, 0.3, scores)
    if top >= 3 and top >= second + 2:
        return RuleResult(best, 0.9, scores)
    if top >= 2 and top > second:
        return RuleResult(best, 0.7, scores)
    return RuleResult(best, 0.4, scores)
