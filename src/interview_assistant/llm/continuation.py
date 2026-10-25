"""Remove an exact repeated opening when a continuation restarts existing text."""


class ContinuationText:
    def __init__(self, previous: str) -> None:
        # Match the full prior answer or a substantial repeated last line/paragraph.
        # Short overlaps (spaces, punctuation, code tokens) may be intentional: preserve them.
        self._candidates = [previous] if len(previous) >= 8 else []
        self._candidates.extend(
            previous[index + 1 :]
            for index, char in enumerate(previous)
            if char == "\n" and 24 <= len(previous) - index - 1 <= 2000
        )
        self._pending = ""
        self._resolved = not self._candidates

    def feed(self, delta: str, final: bool = False) -> str:
        if self._resolved:
            return delta
        self._pending += delta
        if not final and any(candidate.startswith(self._pending) for candidate in self._candidates):
            return ""
        overlap = max(
            (
                len(candidate)
                for candidate in self._candidates
                if self._pending.startswith(candidate)
            ),
            default=0,
        )
        result = self._pending[overlap:]
        self._pending = ""
        self._resolved = True
        return result
