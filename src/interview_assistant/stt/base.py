import io
import wave
from dataclasses import dataclass
from typing import Protocol

import numpy as np

from interview_assistant.audio.types import TARGET_RATE, DeviceKind


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
    # Step 19: which audio source this came from, ONLY set when more than one can be captured at
    # once (simultaneous mic+loopback). None in the app's historical single-source mode - callers
    # use that distinction, not the literal device kind, to decide whether to tag a question's
    # source at all (see AssistantController.on_transcript_event).
    source: DeviceKind | None = None


def pcm_to_wav(pcm: np.ndarray, rate: int = TARGET_RATE) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.astype(np.int16).tobytes())
    return buf.getvalue()
