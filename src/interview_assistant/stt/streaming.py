import logging
import threading
from collections import deque
from collections.abc import Callable

import numpy as np

from interview_assistant.audio.types import TARGET_RATE, DeviceKind
from interview_assistant.stt.base import STTProvider, TranscriptEvent

log = logging.getLogger(__name__)

EventCallback = Callable[[TranscriptEvent], None]

WARN_BACKLOG = 4  # utterances waiting: recognition is slower than the conversation
MAX_BACKLOG = 20  # hard cap; beyond this the oldest speech is dropped


class StreamingTranscriber:
    """Turns VAD output into partial + final transcript events on a background worker.

    - speech_started()              -> begins a new utterance
    - submit_partial(audio_so_far)  -> newest partial replaces any queued (stale) one
    - submit_final(full_segment)    -> always transcribed, before pending partials
    Events for one utterance are ordered: partials..., then exactly one final.
    """

    def __init__(
        self,
        provider: STTProvider,
        on_event: EventCallback,
        on_error: Callable[[Exception], None] | None = None,
        partials: bool = True,
        source: DeviceKind | None = None,
    ) -> None:
        self._provider = provider
        self._partials = partials
        self._on_event = on_event
        self._on_error = on_error
        self._source = source
        self._cv = threading.Condition()
        self._finals: deque[tuple[int, np.ndarray]] = deque()
        self._partial: tuple[int, np.ndarray] | None = None
        self._finalized: set[int] = set()
        self._utt = 0
        self._stop = False
        self._busy = False
        self._warned = False
        self._thread = threading.Thread(target=self._run, name="stt-worker", daemon=True)

    def start(self) -> None:
        if not self._thread.is_alive():
            self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        with self._cv:
            self._stop = True
            self._cv.notify_all()
        if self._thread.is_alive():
            self._thread.join(timeout)

    def speech_started(self) -> None:
        with self._cv:
            self._utt += 1

    def submit_partial(self, audio: np.ndarray) -> None:
        if not self._partials:  # e.g. billed cloud STT: partials re-send the whole utterance
            return
        with self._cv:
            self._partial = (self._utt, audio)
            self._cv.notify()

    def submit_final(self, audio: np.ndarray) -> None:
        with self._cv:
            utt = self._utt
            if self._partial and self._partial[0] == utt:
                self._partial = None  # superseded by the final
            self._finalized.add(utt)
            if len(self._finalized) > 500:  # only recent utterances can still get stale partials
                self._finalized = {u for u in self._finalized if u > utt - 200}
            self._finals.append((utt, audio))
            behind = len(self._finals)
            if behind > MAX_BACKLOG:  # an answer minutes late is useless: drop the oldest speech
                dropped_utt, _ = self._finals.popleft()
                log.warning("STT backlog above %d: dropped utterance %d", MAX_BACKLOG, dropped_utt)
            self._cv.notify()
        # Tell the user once per episode that recognition cannot keep up with the speech.
        if behind >= WARN_BACKLOG and not self._warned:
            self._warned = True
            if self._on_error:
                self._on_error(
                    RuntimeError(
                        "Speech recognition is falling behind the conversation. "
                        "Choose a faster speech model in Settings."
                    )
                )
        elif behind <= 1:
            self._warned = False

    def idle(self) -> bool:
        with self._cv:
            return not self._finals and self._partial is None and not self._busy

    def _next_job(self) -> tuple[int, np.ndarray, bool] | None:
        with self._cv:
            while not self._stop and not self._finals and self._partial is None:
                self._cv.wait()
            if self._stop and not self._finals:
                return None  # drain remaining finals on stop, drop partials
            self._busy = True
            if self._finals:
                utt, audio = self._finals.popleft()
                return utt, audio, True
            assert self._partial is not None
            utt, audio = self._partial
            self._partial = None
            return utt, audio, False

    def _run(self) -> None:
        while (job := self._next_job()) is not None:
            utt, audio, is_final = job
            try:
                text = self._provider.transcribe(audio)
                with self._cv:
                    stale = not is_final and utt in self._finalized
                if text and not stale:
                    self._on_event(
                        TranscriptEvent(utt, text, is_final, len(audio) / TARGET_RATE, self._source)
                    )
                elif is_final:  # keep the utterance lifecycle observable even when empty
                    self._on_event(
                        TranscriptEvent(utt, "", True, len(audio) / TARGET_RATE, self._source)
                    )
            except Exception as e:
                log.exception("Transcription failed")
                if self._on_error:
                    self._on_error(e)
            finally:
                with self._cv:
                    self._busy = False
