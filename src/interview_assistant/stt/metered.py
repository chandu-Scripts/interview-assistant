from collections.abc import Callable

import numpy as np

from interview_assistant.audio.types import TARGET_RATE
from interview_assistant.stt.base import STTProvider


class MeteredSTT:
    """Wraps an STT provider and reports every call's audio duration for cost tracking."""

    def __init__(
        self, inner: STTProvider, on_usage: Callable[[str, str, float], None], model: str = ""
    ) -> None:
        self._inner = inner
        self._on_usage = on_usage
        self.name = inner.name
        self.model = model or getattr(inner, "model", "")

    def warmup(self) -> None:
        self._inner.warmup()

    def transcribe(self, pcm: np.ndarray) -> str:
        try:
            return self._inner.transcribe(pcm)
        finally:
            # Billed even if the call failed after upload, so count it either way.
            self._on_usage(self.name, self.model, len(pcm) / TARGET_RATE)
