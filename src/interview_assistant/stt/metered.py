from collections.abc import Callable, Iterator
from typing import cast

import numpy as np

from interview_assistant.audio.types import TARGET_RATE
from interview_assistant.stt.base import StreamingSTTProvider, STTProvider


class MeteredSTT:
    """Wraps an STT provider and reports every call's audio duration for cost tracking."""

    def __init__(
        self, inner: STTProvider, on_usage: Callable[[str, str, float], None], model: str = ""
    ) -> None:
        self._inner = inner
        self._on_usage = on_usage
        self.name = inner.name
        self.model = model or getattr(inner, "model", "")
        self.realtime = getattr(inner, "realtime", False)

    def warmup(self) -> None:
        self._inner.warmup()

    def transcribe(self, pcm: np.ndarray) -> str:
        try:
            return self._inner.transcribe(pcm)
        finally:
            # Billed even if the call failed after upload, so count it either way.
            actual = getattr(self._inner, "fallback", self._inner)
            self._on_usage(
                actual.name,
                getattr(actual, "model", "") if actual is not self._inner else self.model,
                len(pcm) / TARGET_RATE,
            )

    def stream(self, chunks: Iterator[np.ndarray], on_partial: Callable[[str], None]) -> str:
        seconds = 0.0

        def measured() -> Iterator[np.ndarray]:
            nonlocal seconds
            for chunk in chunks:
                seconds += len(chunk) / TARGET_RATE
                yield chunk

        try:
            return cast(StreamingSTTProvider, self._inner).stream(measured(), on_partial)
        finally:
            if seconds:
                self._on_usage(self.name, self.model, seconds)
