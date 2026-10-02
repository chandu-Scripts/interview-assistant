import logging
import threading
from collections.abc import Callable
from typing import Any, Protocol

import numpy as np

from interview_assistant.audio.convert import rms_level, to_mono_16k
from interview_assistant.audio.types import AudioDevice
from interview_assistant.audio.vad import SpeechSegmenter, VadConfig

log = logging.getLogger(__name__)

RawCallback = Callable[[bytes], None]


class AudioSource(Protocol):
    """Produces interleaved int16 PCM chunks in the device's native format."""

    def start(self, on_raw: RawCallback) -> None: ...
    def stop(self) -> None: ...


class WasapiSource:
    """Real capture via PortAudio/WASAPI (mic or loopback)."""

    def __init__(self, device: AudioDevice, frames_per_buffer: int = 1024) -> None:
        self.device = device
        self._fpb = frames_per_buffer
        self._pa: Any = None
        self._stream: Any = None

    def start(self, on_raw: RawCallback) -> None:
        import pyaudiowpatch as pa

        def cb(in_data, frame_count, time_info, status):
            on_raw(in_data)
            return (None, pa.paContinue)

        self._pa = pa.PyAudio()
        try:
            self._stream = self._pa.open(
                format=pa.paInt16,
                channels=self.device.channels,
                rate=self.device.sample_rate,
                input=True,
                input_device_index=self.device.index,
                frames_per_buffer=self._fpb,
                stream_callback=cb,
            )
        except Exception:
            self._pa.terminate()
            self._pa = None
            raise

    def alive(self) -> bool:
        """False once PortAudio has ended the stream (device unplugged, disabled or taken over)."""
        try:
            return self._stream is not None and bool(self._stream.is_active())
        except Exception:
            return False

    def stop(self) -> None:
        # Each step may fail on a dead device; stopping must still release everything.
        if self._stream is not None:
            try:
                self._stream.stop_stream()
            except Exception:
                log.warning("stop_stream failed (device gone?)")
            try:
                self._stream.close()
            except Exception:
                log.warning("closing the stream failed")
            self._stream = None
        if self._pa is not None:
            try:
                self._pa.terminate()
            except Exception:
                log.warning("PortAudio terminate failed")
            self._pa = None


class AudioCapture:
    """Explicitly started/stopped capture: source -> mono 16 kHz -> level meter + VAD segments.

    Nothing is captured until start() is called by the user; `recording` is the single source of
    truth the UI must display.
    """

    def __init__(
        self,
        source: AudioSource,
        channels: int,
        sample_rate: int,
        on_segment: Callable[[np.ndarray], None] | None = None,
        on_level: Callable[[float], None] | None = None,
        on_speech_state: Callable[[bool], None] | None = None,
        on_partial: Callable[[np.ndarray], None] | None = None,
        vad_config: VadConfig | None = None,
    ) -> None:
        self._source = source
        self._channels = channels
        self._rate = sample_rate
        self._on_level = on_level
        self._lock = threading.Lock()
        self._recording = False
        self._vad = SpeechSegmenter(
            on_segment=on_segment or (lambda _s: None),
            config=vad_config,
            on_state=on_speech_state,
            on_partial=on_partial,
        )

    @classmethod
    def for_device(cls, device: AudioDevice, **kwargs: Any) -> "AudioCapture":
        return cls(WasapiSource(device), device.channels, device.sample_rate, **kwargs)

    @property
    def recording(self) -> bool:
        return self._recording

    def start(self) -> None:
        with self._lock:
            if self._recording:
                return
            self._recording = True
            try:
                self._source.start(self._handle_raw)
            except Exception:
                self._recording = False
                raise
        log.info("Audio capture started")

    def health(self) -> str | None:
        """None while capture is fine (or not running); otherwise why it has stopped delivering.

        Only the source's own liveness is checked: a quiet loopback device legitimately delivers
        no data during silence, so a "no data for N seconds" rule would raise false alarms."""
        if not self._recording:
            return None
        alive = getattr(self._source, "alive", None)
        if callable(alive) and not alive():
            return "The audio device stopped (unplugged, disabled or in use by another app)."
        return None

    def stop(self) -> None:
        with self._lock:
            if not self._recording:
                return
            self._recording = False
            try:
                self._source.stop()
            finally:
                self._vad.flush()
        log.info("Audio capture stopped")

    def _handle_raw(self, raw: bytes) -> None:
        if not self._recording:
            return
        try:
            pcm = to_mono_16k(raw, self._channels, self._rate)
            if self._on_level:
                self._on_level(rms_level(pcm))
            self._vad.feed(pcm)
        except Exception:
            log.exception("Error processing audio chunk")
