"""Offline semantic retrieval with exact shortcuts and conservative ambiguity handling."""

import logging
import re
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from threading import Lock
from typing import Protocol

import numpy as np
from numpy.typing import NDArray

from interview_assistant.app.practice_library import PracticeAnswer, PracticeLibrary, question_key
from interview_assistant.app.semantic_encoder import LocalSentenceEncoder

log = logging.getLogger(__name__)


class Encoder(Protocol):
    def encode(self, texts: list[str]) -> NDArray[np.float32]: ...


STOP = set(
    "a an the i we you your our in on at of for to did do does have has had was were "
    "is are been with about please tell me project projects work used use using".split()
)
ALIASES = {
    "catalogs": "catalog",
    "catalogues": "catalog",
    "catalogue": "catalog",
    "pipelines": "pipeline",
    "built": "build",
    "building": "build",
    "developed": "build",
    "develop": "build",
    "created": "build",
    "create": "build",
    "dealing": "handle",
    "handled": "handle",
    "handling": "handle",
    "members": "member",
}
ARROW = re.compile(r"<-+>|<-+|-+>|[\u2190\u2192\u2194\u21d0\u21d2\u21d4]")


def normalized(text: str) -> str:
    return question_key(text)


def arrow_direction(symbol: str) -> str:
    if symbol in {"\u2194", "\u21d4"} or (symbol.startswith("<") and symbol.endswith(">")):
        return "both"
    return "left" if symbol.startswith("<") or symbol in {"\u2190", "\u21d0"} else "right"


def lookup_key(text: str) -> str:
    # Cache identity must not erase the operator that the compatibility guard examined.
    return normalized(ARROW.sub(lambda m: f" arrow{arrow_direction(m[0])} ", text))


def ordered_terms(text: str) -> tuple[str, ...]:
    text = normalized(text)
    text = re.sub(r"\b(how many|number of)\b", "count", text)
    text = re.sub(r"\bhow much\b", "amount", text)
    return tuple(ALIASES.get(word, word) for word in text.split() if word not in STOP)


def terms(text: str) -> frozenset[str]:
    return frozenset(ordered_terms(text))


def directional_endpoints(text: str) -> tuple[frozenset[str], frozenset[str]] | None:
    """Recognize explicit source/destination roles, without a list of product names."""
    arrows = list(ARROW.finditer(text))
    if arrows:
        if len(arrows) != 1 or arrow_direction(arrows[0][0]) == "both":
            return None
        arrow = arrows[0]
        source, target = text[: arrow.start()], text[arrow.end() :]
        if arrow_direction(arrow[0]) == "left":
            source, target = target, source
    else:
        text = normalized(text)
        destination = r"(?:to|into|onto|toward|towards)"
        match = re.search(rf"\bfrom (.+?)\s+{destination} (.+)", text)
        if match:
            source, target = match.groups()
        else:
            match = re.search(rf"\b{destination} (.+?)\s+from (.+)", text)
            if not match:
                return None
            target, source = match.groups()

    def endpoint(phrase: str) -> frozenset[str]:
        # Trailing implementation details are not part of the endpoint's name.
        phrase = normalized(phrase)
        phrase = re.split(r"\b(?:using|via|with|during|because|after|before)\b", phrase)[0]
        # A single-letter endpoint (A -> B) is a name, not an article.
        return terms(phrase) or frozenset(phrase.split())

    return endpoint(source), endpoint(target)


def compatible_order(left: str, right: str) -> bool:
    """A high embedding score cannot establish that two entities kept their roles."""
    has_arrow = ARROW.search(left) is not None or ARROW.search(right) is not None
    if has_arrow and lookup_key(left) == lookup_key(right):
        return True
    first, second = directional_endpoints(left), directional_endpoints(right)
    if has_arrow and (first is None or second is None):
        return False
    if first is not None and second is not None:
        source, target = first
        other_source, other_target = second
        if not source or not target or not other_source or not other_target:
            return False
        # Permit expanded names, but a common fragment alone is not the same endpoint.
        if not (source <= other_source or other_source <= source):
            return False
        if not (target <= other_target or other_target <= target):
            return False
        # Shared name fragments (e.g. Azure) do not prove that endpoints stayed put.
        if (source - target) & (other_target - other_source):
            return False
        if (target - source) & (other_source - other_target):
            return False
        return True

    # Outside explicit directional syntax, abstain when shared content changes order.
    # Question/count scaffolding can move freely: "catalog count" == "how many catalogs".
    neutral = {
        "how",
        "what",
        "which",
        "why",
        "when",
        "where",
        "who",
        "count",
        "number",
        "amount",
        "size",
        "volume",
        "big",
        "large",
        "total",
    }
    a, b = ordered_terms(left), ordered_terms(right)
    shared = (set(a) & set(b)) - neutral
    return tuple(dict.fromkeys(word for word in a if word in shared)) == tuple(
        dict.fromkeys(word for word in b if word in shared)
    )


def intent(text: str) -> str:
    """General question-type guard: topic similarity alone cannot establish equivalence."""
    text = normalized(text)
    if text.startswith(("how did ", "how do ", "how does ", "how can ", "how to ")):
        return "method"
    if re.search(r"\b(how many|how much|how big|how large|count|number|size|volume)\b", text):
        return "quantity"
    if text.startswith(("why ", "what reason")):
        return "reason"
    if text.startswith("how "):
        return "method"
    if text.startswith(("what is ", "what are ", "define ", "explain ")):
        return "definition"
    return "other"


def compatible(left: str, right: str) -> bool:
    if not compatible_order(left, right):
        return False

    # A narrower environment asks for a different count even when the topic is identical.
    # Compare scopes symmetrically: a prod-only saved answer cannot answer an overall count.
    def environments(text: str) -> set[str]:
        aliases = {
            "prod": "production",
            "production": "production",
            "dev": "development",
            "development": "development",
            "test": "test",
            "testing": "test",
            "qa": "test",
            "uat": "uat",
            "staging": "staging",
            "stage": "staging",
        }
        return {aliases[word] for word in normalized(text).split() if word in aliases}

    if environments(left) != environments(right):
        return False
    # A saved single question cannot supply the answer to a compound follow-up as well.
    counts = [
        len(re.findall(r"\b(?:what|why|how|when|where|which|who)\b", normalized(text)))
        for text in (left, right)
    ]
    if max(counts) > 1 and counts[0] != counts[1]:
        return False
    changed = terms(left) ^ terms(right)
    if changed & {
        "not",
        "never",
        "without",
        "none",
        "another",
        "other",
        "and",
        "or",
        "before",
        "after",
        "c++",
        "c#",
    }:
        return False
    if any(re.fullmatch(r"\d+(?:\.\d+)*", word) for word in changed):
        return False

    # Preserve the thing being counted, e.g. tables inside a catalog versus catalogs.
    def count_target(text: str) -> str:
        match = re.search(
            r"\bhow many (.+?)(?:\s+(?:did|do|does|have|has|had|were|are|was|is|you|u|we|they|i"
            r"|in|per|for|across)\b|$)",
            normalized(text),
        )
        if not match:
            return ""
        word = match[1].split()[-1]
        return ALIASES.get(word, word)

    first, second = count_target(left), count_target(right)
    if first and second and first != second:
        return False
    a, b = intent(left), intent(right)
    return a == b or "other" in (a, b)


@dataclass(frozen=True)
class PracticeMatch:
    answer: PracticeAnswer | None = None


def agreed_answer(entries: list[PracticeAnswer]) -> PracticeMatch:
    """Legacy identical copies are harmless; conflicting answers must never be guessed."""
    if entries and len({entry.answer for entry in entries}) == 1:
        return PracticeMatch(answer=entries[0])
    return PracticeMatch()


class PracticeMatcher:
    def __init__(
        self,
        library: PracticeLibrary,
        encoder_factory: Callable[[], Encoder] = LocalSentenceEncoder,
    ) -> None:
        self.library = library
        self._lock = Lock()
        self._stamp: tuple[int, int] | None = None
        self._index: list[tuple[PracticeAnswer, str, frozenset[str]]] = []
        self._factory = encoder_factory
        self._encoder: Encoder | None = None
        self._unavailable = False
        self._vectors: NDArray[np.float32] | None = None
        self._queries: OrderedDict[str, PracticeMatch] = OrderedDict()

    def prepare(self) -> None:
        """Warm the local index off the UI thread; user content never leaves the device."""
        with self._lock:
            try:
                self._refresh()
                self._ensure_vectors()
            except (OSError, ValueError):
                log.warning("Saved Q&A index unavailable")

    def lookup(self, question: str) -> PracticeMatch:
        with self._lock:
            return self._lookup(question)

    def _refresh(self) -> None:
        path = self.library.path
        stamp = path.stat() if path.exists() else None
        key = (stamp.st_mtime_ns, stamp.st_size) if stamp else None
        if key != self._stamp:
            index = [
                (entry, normalized(entry.question), terms(entry.question))
                for entry in self.library.entries()
            ]
            self._index, self._stamp = index, key
            self._vectors = None
            self._queries.clear()
        if stamp is None:
            self._index = []
            self._vectors = None
            self._queries.clear()

    def _ensure_vectors(self) -> bool:
        if not self._index or self._unavailable:
            return False
        try:
            if self._encoder is None:
                self._encoder = self._factory()
            if self._vectors is None:
                self._vectors = self._encoder.encode([e.question for e, _, _ in self._index])
            return True
        except Exception:
            # Native model/runtime failures must not prevent normal AI answering.
            log.warning("Local semantic model unavailable; using literal saved-question matching")
            self._unavailable = True
            return False

    def _lookup(self, question: str) -> PracticeMatch:
        self._refresh()
        query_name = normalized(question)
        exact = [
            entry
            for entry, name, _ in self._index
            if name == query_name
            and (
                not (ARROW.search(question) or ARROW.search(entry.question))
                or compatible_order(question, entry.question)
            )
        ]
        if exact:
            return agreed_answer(exact)
        query = terms(question)
        query_order = ordered_terms(question)
        strong = [
            entry
            for entry, _, tokens in self._index
            if tokens == query
            and len(query) >= 2
            and ordered_terms(entry.question) == query_order
            and compatible(question, entry.question)
        ]
        if strong:
            return agreed_answer(strong)
        if not self._ensure_vectors():
            return PracticeMatch()
        assert self._encoder is not None and self._vectors is not None
        try:
            cache_key = lookup_key(question)
            if cache_key in self._queries:
                self._queries.move_to_end(cache_key)
                return self._queries[cache_key]
            vector = self._encoder.encode([question])[0]
            scores = self._vectors @ vector
            result = PracticeMatch()
            # An incompatible high-scoring topic must not block a correct scoped answer.
            # Identical answer copies do not represent competing answers.
            seen_answers: set[str] = set()
            order = []
            for index in np.argsort(-scores):
                if scores[index] < 0.45:  # cannot affect a qualifying match's 0.10 margin
                    break
                candidate = self._index[int(index)][0]
                if (
                    compatible(question, candidate.question)
                    and candidate.answer not in seen_answers
                ):
                    order.append(int(index))
                    seen_answers.add(candidate.answer)
            if not order:
                return self._remember(cache_key, result)
            best = int(order[0])
            entry = self._index[best][0]
            score = float(scores[best])
            margin = score - float(scores[order[1]]) if len(order) > 1 else 1.0
            if score >= 0.55 and margin >= 0.10:
                result = PracticeMatch(answer=entry)
            return self._remember(cache_key, result)
        except Exception:
            log.warning("Local semantic lookup failed; using normal AI routing")
        return PracticeMatch()

    def _remember(self, question: str, result: PracticeMatch) -> PracticeMatch:
        self._queries[question] = result
        if len(self._queries) > 128:
            self._queries.popitem(last=False)
        return result
