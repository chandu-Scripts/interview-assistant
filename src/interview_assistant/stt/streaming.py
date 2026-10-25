import logging
import threading
import time
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
        preview_provider: STTProvider | None = None,
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
        self._warmup = False
        self._thread = threading.Thread(target=self._run, name="stt-worker", daemon=True)
        self._preview = (
            StreamingTranscriber(
                preview_provider, self._on_preview, self._preview_error, source=source
            )
            if preview_provider is not None and partials
            else None
        )
        self._preview_failed = False

    def _on_preview(self, event: TranscriptEvent) -> None:
        # Serialize delivery with final submission so late previews never replace final text.
        with self._cv:
            if (
                not self._stop
                and event.utterance_id == self._utt
                and self._utt not in self._finalized
            ):
                self._on_event(event)

    def _preview_error(self, error: Exception) -> None:
        if not self._preview_failed:
            self._preview_failed = True
            if self._on_error and not self._stop:
                self._on_error(
                    RuntimeError(
                        "Live transcript preview unavailable; final transcription still works."
                    )
                )

    def start(self, warmup: bool = False) -> None:
        self._warmup = warmup
        if self._preview is not None:
            self._preview.start(warmup=True)
        if not self._thread.is_alive():
            self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        deadline = time.monotonic() + timeout
        with self._cv:
            self._stop = True
            self._partial = None
            self._cv.notify_all()
        if self._thread.is_alive():
            self._thread.join(timeout)
        if self._preview is not None:
            self._preview.stop(max(0.0, deadline - time.monotonic()))

    def speech_started(self) -> None:
        with self._cv:
            self._utt += 1
            if self._preview is not None:
                self._preview.speech_started()

    def submit_partial(self, audio: np.ndarray) -> None:
        if not self._partials:  # e.g. billed cloud STT: partials re-send the whole utterance
            return
        with self._cv:
            if self._stop or self._utt in self._finalized:
                return
            if self._preview is not None:
                if not self._preview_failed:
                    self._preview.submit_partial(audio)
                return
            self._partial = (self._utt, audio)
            self._cv.notify()

    def submit_final(self, audio: np.ndarray) -> None:
        dropped: TranscriptEvent | None = None
        with self._cv:
            utt = self._utt
            if self._partial and self._partial[0] == utt:
                self._partial = None  # superseded by the final
            self._finalized.add(utt)
            if self._preview is not None:
                self._preview.discard_preview(utt)
            if len(self._finalized) > 500:  # only recent utterances can still get stale partials
                self._finalized = {u for u in self._finalized if u > utt - 200}
            self._finals.append((utt, audio))
            behind = len(self._finals)
            if behind > MAX_BACKLOG:  # an answer minutes late is useless: drop the oldest speech
                dropped_utt, dropped_audio = self._finals.popleft()
                dropped = TranscriptEvent(
                    dropped_utt, "", True, len(dropped_audio) / TARGET_RATE, self._source
                )
                log.warning("STT backlog above %d: dropped utterance %d", MAX_BACKLOG, dropped_utt)
            self._cv.notify()
        if dropped is not None:
            self._on_event(dropped)
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

    def discard_preview(self, utterance_id: int) -> None:
        """Drop queued speculative work once the final segment is ready."""
        with self._cv:
            if self._partial is not None and self._partial[0] <= utterance_id:
                self._partial = None

    def idle(self) -> bool:
        with self._cv:
            return (
                not self._finals
                and self._partial is None
                and not self._busy
                and (self._preview is None or self._preview.idle())
            )

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
        if self._warmup:
            try:
                self._provider.warmup()
            except Exception as e:
                with self._cv:
                    self._partial = None
                    self._stop = True
                if self._on_error:
                    self._on_error(e)
                return
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
                if is_final:
                    # Retire failed speech as well: otherwise the question merger waits for a
                    # final that will never arrive and can hold the next good question for seconds.
                    self._on_event(
                        TranscriptEvent(utt, "", True, len(audio) / TARGET_RATE, self._source)
                    )
                if self._on_error:
                    self._on_error(e)
            finally:
                with self._cv:
                    self._busy = False
