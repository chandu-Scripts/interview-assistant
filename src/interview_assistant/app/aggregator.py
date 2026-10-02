import time
from collections import deque
from collections.abc import Callable


class UtteranceAggregator:
    """Merges consecutive transcribed utterances into one question.

    An interviewer often pauses mid-question, so the VAD splits it into several utterances. A
    question is released only when (a) nothing is still being transcribed, (b) nothing new has
    arrived for `quiet_s`, or (c) `max_hold_s` has passed since the first fragment.
    Qt-free and clock-injectable; call poll() periodically.
    """

    def __init__(
        self,
        quiet_s: float = 0.5,
        max_hold_s: float = 8.0,
        stale_s: float = 20.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._quiet = quiet_s
        self._max_hold = max_hold_s
        self._stale = stale_s
        self._clock = clock
        self._starts: deque[float] = deque()  # speech started, final transcript not yet received
        self._parts: list[str] = []
        self._first = 0.0
        self._last = 0.0

    @property
    def in_flight(self) -> int:
        return len(self._starts)

    @property
    def pending_text(self) -> str:
        return " ".join(self._parts)

    def speech_started(self) -> None:
        self._starts.append(self._clock())

    def final(self, text: str) -> None:
        if self._starts:
            self._starts.popleft()
        text = text.strip()
        if not text:
            return
        now = self._clock()
        if not self._parts:
            self._first = now
        self._parts.append(text)
        self._last = now

    def poll(self) -> str | None:
        now = self._clock()
        while self._starts and now - self._starts[0] > self._stale:
            self._starts.popleft()  # a transcription that never completed must not block forever
        if not self._parts:
            return None
        waiting = bool(self._starts) and now - self._first < self._max_hold
        if waiting or now - self._last < self._quiet:
            return None
        text = self.pending_text
        self._parts.clear()
        return text

    def reset(self) -> None:
        self._starts.clear()
        self._parts.clear()
