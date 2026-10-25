"""Bounded, cancellable utterance streaming with one active socket per audio source."""

import logging
import threading
import time
from collections import deque
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field

import numpy as np

from interview_assistant.audio.types import TARGET_RATE, DeviceKind
from interview_assistant.stt.base import StreamingSTTProvider, TranscriptEvent
from interview_assistant.stt.streaming import MAX_BACKLOG

log = logging.getLogger(__name__)


@dataclass
class _Utterance:
    ident: int
    audio: np.ndarray = field(default_factory=lambda: np.empty(0, dtype=np.int16))
    final: bool = False
    cancelled: bool = False


class RealtimeTranscriber:
    def __init__(
        self,
        provider: StreamingSTTProvider,
        on_event: Callable[[TranscriptEvent], None],
        on_error: Callable[[Exception], None] | None = None,
        source: DeviceKind | None = None,
    ) -> None:
        self._provider = provider
        self._on_event = on_event
        self._on_error = on_error
        self._source = source
        self._cv = threading.Condition()
        self._queue: deque[_Utterance] = deque()
        self._current: _Utterance | None = None
        self._utt = 0
        self._stopped = False
        self._busy = False
        self._generation = 0
        self._retry_at = 0.0
        self._thread = threading.Thread(target=self._run, name="realtime-stt", daemon=True)

    def start(self, warmup: bool = False) -> None:
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self.cancel()
        with self._cv:
            self._stopped = True
            self._cv.notify_all()
        self._thread.join(min(timeout, 0.2))

    def cancel(self) -> None:
        """Stop/presentation invalidates pending text without blocking the Qt event loop."""
        with self._cv:
            self._generation += 1
            self._queue.clear()
            self._current = None
            self._cv.notify_all()

    def speech_started(self) -> None:
        with self._cv:
            if self._stopped:
                return
            self._utt += 1
            self._current = _Utterance(self._utt)
            self._queue.append(self._current)
            if len(self._queue) > MAX_BACKLOG:
                dropped = self._queue.popleft()
                self._emit(dropped, "", True)
                if self._on_error:
                    self._on_error(RuntimeError("Transcription backlog: oldest speech dropped."))
            self._cv.notify_all()

    def submit_partial(self, audio: np.ndarray) -> None:
        with self._cv:
            if self._current is not None and not self._current.final:
                self._current.audio = audio
                self._cv.notify_all()

    def submit_final(self, audio: np.ndarray) -> None:
        with self._cv:
            if self._current is not None:
                self._current.audio = audio
                self._current.final = True
                self._cv.notify_all()

    def idle(self) -> bool:
        with self._cv:
            return not self._queue and not self._busy

    def _valid(self, generation: int) -> bool:
        return not self._stopped and generation == self._generation

    def _emit(self, utterance: _Utterance, text: str, final: bool) -> None:
        self._on_event(
            TranscriptEvent(
                utterance.ident,
                text,
                final,
                len(utterance.audio) / TARGET_RATE,
                self._source,
            )
        )

    def _chunks(self, utterance: _Utterance, generation: int) -> Iterator[np.ndarray]:
        sent = 0
        while True:
            with self._cv:

                def available(sent: int = sent) -> bool:
                    return (
                        not self._valid(generation)
                        or utterance.cancelled
                        or len(utterance.audio) > sent
                        or utterance.final
                    )

                self._cv.wait_for(available)
                if not self._valid(generation) or utterance.cancelled:
                    raise RuntimeError("Audio stream cancelled")
                chunk = utterance.audio[sent:]
                sent = len(utterance.audio)
                final = utterance.final
            if len(chunk):
                yield chunk
            if final:
                return

    def _run(self) -> None:
        while True:
            with self._cv:
                self._cv.wait_for(lambda: self._stopped or bool(self._queue))
                if self._stopped:
                    return
                utterance = self._queue.popleft()
                generation = self._generation
                self._busy = True

            def preview(
                text: str,
                utterance: _Utterance = utterance,
                generation: int = generation,
            ) -> None:
                with self._cv:
                    if text and self._valid(generation) and not utterance.cancelled:
                        self._emit(utterance, text, False)

            fallback_failed = False
            try:
                if time.monotonic() < self._retry_at:
                    raise RuntimeError("Speechmatics reconnect cooldown")
                text = self._provider.stream(self._chunks(utterance, generation), preview)
            except Exception:
                with self._cv:
                    utterance.cancelled = True
                    self._cv.notify_all()
                    valid = self._valid(generation)
                    if valid and time.monotonic() >= self._retry_at:
                        self._retry_at = time.monotonic() + 10
                    if valid and self._on_error:
                        self._on_error(
                            RuntimeError(
                                "Live transcription interrupted. Using fallback; "
                                "text may appear after you finish speaking."
                            )
                        )
                with self._cv:

                    def ready_for_fallback(
                        u: _Utterance = utterance,
                        g: int = generation,
                    ) -> bool:
                        return u.final or not self._valid(g)

                    self._cv.wait_for(ready_for_fallback)
                    valid = self._valid(generation)
                text = ""
                if valid:
                    try:
                        text = self._provider.transcribe(utterance.audio)
                    except Exception:
                        fallback_failed = True
                        log.warning("Fallback transcription failed (source=%s)", self._source)
            finally:
                with self._cv:
                    utterance.cancelled = True
                    self._cv.notify_all()
            with self._cv:
                if self._valid(generation):
                    self._emit(utterance, text, True)
                    # Emit after the empty final so the useful failure is not replaced by
                    # "no words". Cancellation must suppress late errors as well as text.
                    if fallback_failed and self._on_error:
                        self._on_error(
                            RuntimeError(
                                "Live and fallback transcription failed. Check Audio settings."
                            )
                        )
                self._busy = False
                self._cv.notify_all()
