"""Measure local semantic retrieval with synthetic Q&A, without network or user data."""

import json
import tempfile
import time
from pathlib import Path

from interview_assistant.app.practice_library import PracticeLibrary
from interview_assistant.app.practice_matcher import PracticeMatcher


def main() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        library = PracticeLibrary(Path(temporary) / "qa.json", lambda: True)
        library.add_examples()
        matcher = PracticeMatcher(library)
        start = time.perf_counter()
        matcher.prepare()
        print(f"Model load + initial index: {(time.perf_counter() - start) * 1000:.1f} ms")
        for question in (
            "What was your catalog count?",
            "How many catalogs u have sed?",
            "What volume of data did you process each day?",
            "How big was the team you worked with?",
            "What is Unity Catalog?",
        ):
            start = time.perf_counter()
            match = matcher.lookup(question)
            elapsed = (time.perf_counter() - start) * 1000
            start = time.perf_counter()
            matcher.lookup(question)
            cached = (time.perf_counter() - start) * 1000
            print(
                json.dumps(
                    {
                        "question": question,
                        "matched": bool(match.answer),
                        "lookup_ms": round(elapsed, 2),
                        "cached_ms": round(cached, 2),
                    }
                )
            )


if __name__ == "__main__":
    main()
