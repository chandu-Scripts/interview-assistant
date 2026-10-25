"""Local, explicitly fictional interview practice questions and answers."""

import json
import os
import re
import tempfile
import unicodedata
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from uuid import uuid4


@dataclass(frozen=True)
class PracticeAnswer:
    id: str
    question: str
    answer: str


MAX_IMPORT_BYTES = 1_048_576


def question_key(text: str) -> str:
    """Ignore presentation punctuation, preserving technical names such as C++ and C#."""
    text = unicodedata.normalize("NFKC", text).casefold()
    text = re.sub(r"\b(\w+)n['’]t\b", r"\1 not", text)
    return " ".join(re.findall(r"\w+(?:\.\w+)*(?:[+#]+)?", text))


def parse_qa_json(text: str) -> list[tuple[str, str]]:
    """Validate the whole batch before allowing any persistent change."""
    if len(text.encode("utf-8")) > MAX_IMPORT_BYTES:
        raise ValueError("Use a JSON file or paste smaller than 1 MB.")

    def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        seen = set()
        for key, value in pairs:
            normalized = question_key(key)
            if normalized in seen:
                raise ValueError("Duplicate question in JSON. Keep one answer per question.")
            seen.add(normalized)
            result[key] = value
        return result

    try:
        data = json.loads(text.lstrip("\ufeff"), object_pairs_hook=unique_object)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON at line {e.lineno}, column {e.colno}: {e.msg}.") from e
    except RecursionError as e:
        raise ValueError("Use a flat JSON object with question keys and text answers.") from e
    if not isinstance(data, dict) or not data:
        raise ValueError('Use a non-empty JSON object: {"Question?": "Answer"}.')
    if len(data) > 1000:
        raise ValueError("Import up to 1,000 questions at a time.")
    result = []
    for number, (question, answer) in enumerate(data.items(), 1):
        if not question_key(question):
            raise ValueError(f"Entry {number}: a question is compulsory.")
        if not isinstance(answer, str) or not answer.strip():
            raise ValueError(f"Entry {number}: a non-empty text answer is compulsory.")
        question, answer = question.strip(), answer.strip()
        if len(question) > 1000 or len(answer) > 12000:
            raise ValueError(
                f"Entry {number}: limit questions to 1,000 and answers to 12,000 characters."
            )
        result.append((question, answer))
    return result


EXAMPLES = (
    (
        "How many catalogs did you use?",
        "We had three catalogs: dev, test and prod. I mainly worked in dev. "
        "We tested changes in test before promoting them to prod through our release process.",
    ),
    (
        "How many pipelines did you build?",
        "Our team maintained about 20 pipelines. I built six of those and maintained a few "
        "existing ones. Most of mine handled daily ingestion; two processed incremental changes.",
    ),
    (
        "How much data were you dealing with?",
        "A normal daily load was around 40 GB across our sources. The initial historical load "
        "was much larger, roughly 2 TB. We treated the backfill separately so it would not "
        "hold up the daily runs.",
    ),
    (
        "What was your team size?",
        "There were six of us: three data engineers, a tech lead, a QA engineer and an analyst. "
        "I was one of the data engineers. I owned a set of ingestion pipelines and worked "
        "with the analyst to check the output against business requirements.",
    ),
)


class PracticeLibrary:
    def __init__(self, path: Path, can_write: Callable[[], bool]) -> None:
        self.path = path
        self._can_write = can_write

    @property
    def writable(self) -> bool:
        return self._can_write()

    def entries(self) -> list[PracticeAnswer]:
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, list):
                raise ValueError()
            result = []
            seen = set()
            for item in data:
                if not isinstance(item, dict) or any(
                    not isinstance(item.get(key), str) or not item[key].strip()
                    for key in ("id", "question", "answer")
                ):
                    raise ValueError()
                if item["id"] in seen:
                    raise ValueError()
                seen.add(item["id"])
                result.append(PracticeAnswer(item["id"], item["question"], item["answer"]))
            return result
        except (ValueError, TypeError, KeyError) as e:
            raise ValueError("Cannot read the Q&A library. The original file is preserved.") from e

    def save(self, entry_id: str | None, question: str, answer: str) -> str:
        question, answer = question.strip(), answer.strip()
        if not question_key(question) or not answer:
            raise ValueError("Enter both a question and an answer.")
        if len(question) > 1000 or len(answer) > 12000:
            raise ValueError("Use up to 1,000 characters for a question and 12,000 for an answer.")
        entries = self.entries()
        if entry_id is not None and not any(e.id == entry_id for e in entries):
            raise ValueError("This entry no longer exists. Start a new question.")
        if any(
            e.id != entry_id and question_key(e.question) == question_key(question) for e in entries
        ):
            raise ValueError("This question is already saved. Edit the existing question instead.")
        entry_id = entry_id or uuid4().hex
        entry = PracticeAnswer(entry_id, question, answer)
        self._write(
            [e if e.id != entry_id else entry for e in entries]
            if any(e.id == entry_id for e in entries)
            else [*entries, entry]
        )
        return entry_id

    def delete(self, entry_id: str) -> None:
        self._write([e for e in self.entries() if e.id != entry_id])

    def import_json(self, text: str) -> tuple[int, int]:
        pairs = parse_qa_json(text)
        if not self.writable:
            raise ValueError("Turn off Incognito in Privacy to change saved Q&A.")
        entries = self.entries()
        existing = {question_key(e.question) for e in entries}
        additions = [
            PracticeAnswer(uuid4().hex, q, a) for q, a in pairs if question_key(q) not in existing
        ]
        if additions:
            self._write([*entries, *additions])
        return len(additions), len(pairs) - len(additions)

    def add_examples(self) -> None:
        entries = self.entries()
        questions = {question_key(e.question) for e in entries}
        self._write(
            [
                *entries,
                *(
                    PracticeAnswer(uuid4().hex, q, a)
                    for q, a in EXAMPLES
                    if question_key(q) not in questions
                ),
            ]
        )

    def _write(self, entries: list[PracticeAnswer]) -> None:
        if not self.writable:
            raise ValueError("Turn off Incognito in Privacy to change saved Q&A.")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix=".practice-",
                suffix=".tmp",
                delete=False,
            ) as stream:
                temporary = Path(stream.name)
                json.dump([asdict(e) for e in entries], stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(self.path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
