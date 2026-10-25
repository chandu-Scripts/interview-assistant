import io
import re
import wave
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Protocol

import numpy as np

from interview_assistant.audio.types import TARGET_RATE, DeviceKind

NO_WORDS_MESSAGE = "No words recognized. Speak again; check the audio input if this repeats."


class STTProvider(Protocol):
    """Turns 16 kHz mono int16 audio into text. Must be safe to call from one worker thread."""

    name: str

    def transcribe(self, pcm: np.ndarray) -> str: ...

    def warmup(self) -> None:
        """Optional: load models / open connections ahead of the first request."""
        ...


class STTError(RuntimeError):
    pass


class StreamingSTTProvider(STTProvider, Protocol):
    def stream(self, chunks: Iterator[np.ndarray], on_partial: Callable[[str], None]) -> str: ...


def is_prompt_echo(text: str, prompt: str | None) -> bool:
    """Reject long vocabulary-hint copies, not ordinary speech containing technical terms."""
    if not prompt:
        return False
    words = re.findall(r"\w+", text.casefold())
    hint = re.findall(r"\w+", prompt.casefold())
    if len(words) < 8 or len(hint) < 8:
        return False
    # Real questions can quote a vocabulary list. Preserve their question/instruction wording.
    cues = {
        "what",
        "why",
        "how",
        "when",
        "where",
        "which",
        "who",
        "explain",
        "describe",
        "compare",
        "define",
        "implement",
        "write",
        "can",
        "could",
    }
    if (set(words) - set(hint)) & cues:
        return False
    blocks = SequenceMatcher(None, words, hint, autojunk=False).get_matching_blocks()
    return max(block.size for block in blocks) >= 8 and (
        sum(block.size for block in blocks) / len(words) >= 0.9
    )


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
