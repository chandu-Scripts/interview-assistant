import io
import wave
from dataclasses import dataclass
from typing import Protocol

import numpy as np

from interview_assistant.audio.types import TARGET_RATE


class STTProvider(Protocol):
    """Turns 16 kHz mono int16 audio into text. Must be safe to call from one worker thread."""

    name: str

    def transcribe(self, pcm: np.ndarray) -> str: ...

    def warmup(self) -> None:
        """Optional: load models / open connections ahead of the first request."""
        ...


class STTError(RuntimeError):
    pass


@dataclass(frozen=True)
class TranscriptEvent:
    utterance_id: int
    text: str
    is_final: bool
    duration_s: float


def pcm_to_wav(pcm: np.ndarray, rate: int = TARGET_RATE) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.astype(np.int16).tobytes())
    return buf.getvalue()
