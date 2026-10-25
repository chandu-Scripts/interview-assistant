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


def normalized(text: str) -> str:
    return question_key(text)


def terms(text: str) -> frozenset[str]:
    text = normalized(text)
    text = re.sub(r"\b(how many|number of)\b", "count", text)
    text = re.sub(r"\bhow much\b", "amount", text)
    return frozenset(ALIASES.get(word, word) for word in text.split() if word not in STOP)


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
        exact = [entry for entry, name, _ in self._index if name == query_name]
        if exact:
            return agreed_answer(exact)
        query = terms(question)
        strong = [
            entry
            for entry, _, tokens in self._index
            if tokens == query and len(query) >= 2 and compatible(question, entry.question)
        ]
        if strong:
            return agreed_answer(strong)
        if not self._ensure_vectors():
            return PracticeMatch()
        assert self._encoder is not None and self._vectors is not None
        try:
            if query_name in self._queries:
                self._queries.move_to_end(query_name)
                return self._queries[query_name]
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
                return self._remember(query_name, result)
            best = int(order[0])
            entry = self._index[best][0]
            score = float(scores[best])
            margin = score - float(scores[order[1]]) if len(order) > 1 else 1.0
            if score >= 0.55 and margin >= 0.10:
                result = PracticeMatch(answer=entry)
            return self._remember(query_name, result)
        except Exception:
            log.warning("Local semantic lookup failed; using normal AI routing")
        return PracticeMatch()

    def _remember(self, question: str, result: PracticeMatch) -> PracticeMatch:
        self._queries[question] = result
        if len(self._queries) > 128:
            self._queries.popitem(last=False)
        return result
