import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass
class _SourceState:
    starts: deque[float] = field(default_factory=deque)  # speech started, final not yet received
    parts: list[str] = field(default_factory=list)
    first: float = 0.0
    last: float = 0.0
    ended: deque[float] = field(default_factory=lambda: deque(maxlen=64))


class UtteranceAggregator:
    """Merges consecutive transcribed utterances into one question, per audio source.

    An interviewer often pauses mid-question, so the VAD splits it into several utterances. A
    question is released only when (a) nothing is still being transcribed, (b) nothing new has
    arrived for `quiet_s`, or (c) `max_hold_s` has passed since the first fragment.

    Step 19: bookkeeping is keyed by `source` (an arbitrary string the caller chooses, e.g. a
    DeviceKind's value) so a mic utterance and a loopback utterance in flight concurrently can't
    merge into each other's text or interfere with each other's quiet/stale timers. The default
    source ("") keeps every single-source caller (speech_started(), final(text), the whole app
    before Step 19) working exactly as it always did - this class has no idea what a "mic" or
    "loopback" even is, it just keys its state by whatever string it's given.

    Qt-free and clock-injectable; call poll() periodically.
    """

    def __init__(
        self,
        quiet_s: float = 0.25,
        max_hold_s: float = 8.0,
        stale_s: float = 20.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._quiet = quiet_s
        self._max_hold = max_hold_s
        self._stale = stale_s
        self._clock = clock
        self._by_source: dict[str, _SourceState] = {}

    def _state(self, source: str) -> _SourceState:
        return self._by_source.setdefault(source, _SourceState())

    @property
    def in_flight(self) -> int:
        return sum(len(s.starts) for s in self._by_source.values())

    @property
    def pending_text(self) -> str:
        """The default source's ("") pending text - kept for single-source callers/tests."""
        return " ".join(self._by_source[""].parts) if "" in self._by_source else ""

    def speech_started(self, source: str = "") -> None:
        self._state(source).starts.append(self._clock())

    def speech_ended(self, source: str = "") -> None:
        st = self._state(source)
        if len(st.ended) < len(st.starts):
            st.ended.append(self._clock())

    def final(self, text: str, source: str = "") -> None:
        st = self._state(source)
        if st.starts:
            st.starts.popleft()
        ended_at = st.ended.popleft() if st.ended else None
        text = text.strip()
        if not text:
            return
        now = self._clock()
        if not st.parts:
            st.first = now
        st.parts.append(text)
        # Count transcription time toward the merge window instead of waiting again afterwards.
        st.last = ended_at if ended_at is not None else now

    def poll(self) -> tuple[str, str] | None:
        """(text, source) for the first source whose merge window has closed, or None. Sources
        are checked in a stable (insertion) order; if more than one is ready in the same tick,
        the other(s) are simply picked up on the next poll() a moment later."""
        now = self._clock()
        for source, st in self._by_source.items():
            while st.starts and now - st.starts[0] > self._stale:
                st.starts.popleft()  # a transcription that never completed must not block forever
            if not st.parts:
                continue
            waiting = bool(st.starts) and now - st.first < self._max_hold
            if waiting or now - st.last < self._quiet:
                continue
            text = " ".join(st.parts)
            st.parts.clear()
            return text, source
        return None

    def reset(self, source: str | None = None) -> None:
        if source is None:
            self._by_source.clear()
        else:
            self._by_source.pop(source, None)
