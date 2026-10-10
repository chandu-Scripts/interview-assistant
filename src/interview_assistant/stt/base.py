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


def is_mostly_latin_script(text: str, threshold: float = 0.8) -> bool:
    """True if `text` reads as Latin-script speech (English and friends) rather than a different
    script (Devanagari, Telugu, Arabic, etc.). Used to drop a transcription that ignored a
    language locked to "en" - the model occasionally mis-transcribes into another script despite
    the language hint, especially on short or accented audio."""
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return True
    latin = sum(1 for c in letters if c.isascii() or 0x00C0 <= ord(c) <= 0x024F)
    return (latin / len(letters)) >= threshold


def pcm_to_wav(pcm: np.ndarray, rate: int = TARGET_RATE) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.astype(np.int16).tobytes())
    return buf.getvalue()
