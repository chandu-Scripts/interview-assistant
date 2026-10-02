from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from interview_assistant.audio.convert import rms_level
from interview_assistant.audio.types import TARGET_RATE

FRAME_MS = 30
FRAME_SAMPLES = TARGET_RATE * FRAME_MS // 1000


@dataclass
class VadConfig:
    threshold: float = 0.01  # RMS (0..1) above which a frame counts as speech
    start_frames: int = 3  # consecutive speech frames to open a segment
    end_silence_ms: int = 600  # trailing silence that closes a segment
    pre_roll_ms: int = 300  # audio kept from before the segment opened
    max_segment_s: float = 30.0  # force-close very long segments
    partial_interval_ms: int = 1500  # how often on_partial fires while speech is ongoing


class SpeechSegmenter:
    """Energy-based VAD with hysteresis. Feed 16 kHz mono int16; get whole speech segments."""

    def __init__(
        self,
        on_segment: Callable[[np.ndarray], None],
        config: VadConfig | None = None,
        on_state: Callable[[bool], None] | None = None,
        on_partial: Callable[[np.ndarray], None] | None = None,
    ) -> None:
        self.cfg = config or VadConfig()
        self._on_segment = on_segment
        self._on_state = on_state
        self._on_partial = on_partial
        self._since_partial_ms = 0
        self._buf = np.empty(0, dtype=np.int16)
        self._pre: deque[np.ndarray] = deque(maxlen=max(1, self.cfg.pre_roll_ms // FRAME_MS))
        self._segment: list[np.ndarray] = []
        self._speech_run = 0
        self._silence_ms = 0
        self._active = False

    @property
    def active(self) -> bool:
        return self._active

    def feed(self, pcm: np.ndarray) -> None:
        self._buf = np.concatenate([self._buf, pcm])
        while len(self._buf) >= FRAME_SAMPLES:
            frame, self._buf = self._buf[:FRAME_SAMPLES], self._buf[FRAME_SAMPLES:]
            self._process(frame)

    def flush(self) -> None:
        """Close any open segment (call on stop)."""
        if self._active:
            self._close()
        self._buf = np.empty(0, dtype=np.int16)
        self._pre.clear()
        self._speech_run = 0

    def _process(self, frame: np.ndarray) -> None:
        is_speech = rms_level(frame) >= self.cfg.threshold
        if not self._active:
            self._pre.append(frame)
            self._speech_run = self._speech_run + 1 if is_speech else 0
            if self._speech_run >= self.cfg.start_frames:
                self._active = True
                self._segment = list(self._pre)
                self._pre.clear()
                self._silence_ms = 0
                self._since_partial_ms = 0
                if self._on_state:
                    self._on_state(True)
            return
        self._segment.append(frame)
        self._silence_ms = 0 if is_speech else self._silence_ms + FRAME_MS
        self._since_partial_ms += FRAME_MS
        too_long = len(self._segment) * FRAME_MS >= self.cfg.max_segment_s * 1000
        if self._silence_ms >= self.cfg.end_silence_ms or too_long:
            self._close()
        elif self._on_partial and self._since_partial_ms >= self.cfg.partial_interval_ms:
            self._since_partial_ms = 0
            self._on_partial(np.concatenate(self._segment))

    def _close(self) -> None:
        seg = np.concatenate(self._segment) if self._segment else np.empty(0, np.int16)
        self._segment = []
        self._active = False
        self._speech_run = 0
        self._silence_ms = 0
        if self._on_state:
            self._on_state(False)
        if len(seg):
            self._on_segment(seg)
