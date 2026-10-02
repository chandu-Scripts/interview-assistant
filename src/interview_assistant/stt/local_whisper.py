import logging
import threading
from typing import Any

import numpy as np

from interview_assistant.stt.base import STTError

log = logging.getLogger(__name__)


class FasterWhisperProvider:
    """Offline transcription with faster-whisper (CTranslate2). No API key, audio stays local."""

    name = "local-whisper"
    metered = False

    def __init__(
        self,
        model_size: str = "base.en",
        device: str = "cpu",
        compute_type: str = "int8",
        language: str | None = "en",
        initial_prompt: str | None = None,
    ) -> None:
        self._model_size = model_size
        self._initial_prompt = initial_prompt or None
        self.model = model_size
        self._device = device
        self._compute_type = compute_type
        self._language = language
        self._model: Any = None
        self._lock = threading.Lock()

    def warmup(self) -> None:
        with self._lock:
            if self._model is None:
                from faster_whisper import WhisperModel

                log.info("Loading whisper model %s", self._model_size)
                self._model = WhisperModel(
                    self._model_size, device=self._device, compute_type=self._compute_type
                )

    def transcribe(self, pcm: np.ndarray) -> str:
        if len(pcm) == 0:
            return ""
        try:
            self.warmup()
            audio = pcm.astype(np.float32) / 32768.0
            segments, _info = self._model.transcribe(
                audio,
                language=self._language,
                beam_size=1,
                vad_filter=True,  # drops silence -> avoids "Thank you." hallucinations
                condition_on_previous_text=False,
                initial_prompt=self._initial_prompt,  # vocabulary hint (see settings)
            )
            return " ".join(s.text.strip() for s in segments).strip()
        except Exception as e:
            raise STTError(f"local whisper failed: {e}") from e
