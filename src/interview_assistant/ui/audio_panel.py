import logging
from collections.abc import Callable

import numpy as np
from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from interview_assistant.audio.capture import AudioCapture
from interview_assistant.audio.devices import list_devices
from interview_assistant.audio.types import AudioDevice, DeviceKind

log = logging.getLogger(__name__)

CaptureFactory = Callable[..., AudioCapture]


class AudioPanel(QWidget):
    """Device selection + explicit Start/Stop + level meter + speech indicator."""

    recording_changed = Signal(bool)
    capture_failed = Signal(str)  # the device died while recording
    level_changed = Signal(float)
    speech_changed = Signal(bool)
    segment_ready = Signal(object)  # np.ndarray, 16 kHz mono int16
    partial_ready = Signal(object)  # audio of the ongoing utterance so far

    def __init__(
        self,
        devices: list[AudioDevice] | None = None,
        capture_factory: CaptureFactory = AudioCapture.for_device,
        default_kind: DeviceKind | None = None,
        restrict_kind: DeviceKind | None = None,
    ) -> None:
        super().__init__()
        self._default_kind = default_kind
        # Step 19: when set, the combo only ever lists devices of this kind - used to make this
        # panel a fixed-role "Microphone" or "System audio" row (composition.py constructs one of
        # each for simultaneous capture) rather than a free pick-anything device selector. None
        # (the default) keeps today's exact single-panel, pick-any-device behavior.
        self._restrict_kind = restrict_kind
        self._factory = capture_factory
        # Optional gate run before capture starts (e.g. the recording-consent notice).
        self.before_start: Callable[[], bool] | None = None
        self._capture: AudioCapture | None = None
        self._segments = 0

        self.device_combo = QComboBox()
        self.refresh_button = QPushButton("Refresh")
        self.toggle_button = QPushButton("Start capture")
        self.recording_label = QLabel("Not recording")
        self.speech_label = QLabel("Speech: no")
        self.segments_label = QLabel("Segments: 0")
        self.level_bar = QProgressBar()
        self.level_bar.setRange(0, 100)
        self.level_bar.setTextVisible(False)
        self.error_label = QLabel("")

        top = QHBoxLayout()
        top.addWidget(self.device_combo, 1)
        top.addWidget(self.refresh_button)
        row = QHBoxLayout()
        row.addWidget(self.toggle_button)
        row.addWidget(self.recording_label)
        row.addStretch(1)
        row.addWidget(self.speech_label)
        row.addWidget(self.segments_label)
        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addLayout(row)
        layout.addWidget(self.level_bar)
        layout.addWidget(self.error_label)

        # While recording, check once a second that the device is still delivering audio.
        self._watchdog = QTimer(self)
        self._watchdog.setInterval(1000)
        self._watchdog.timeout.connect(self.check_health)
        self._watchdog.start()

        self.refresh_button.clicked.connect(self.refresh_devices)
        self.toggle_button.clicked.connect(self.toggle)
        self.level_changed.connect(self._on_level)
        self.speech_changed.connect(self._on_speech)
        self.segment_ready.connect(self._on_segment)
        self.refresh_devices(devices)

    @property
    def recording(self) -> bool:
        return self._capture is not None and self._capture.recording

    def refresh_devices(self, devices: list[AudioDevice] | None = None) -> None:
        if self.recording:
            return
        devs = devices if isinstance(devices, list) else list_devices()
        if self._restrict_kind is not None:
            devs = [d for d in devs if d.kind is self._restrict_kind]
        self.device_combo.clear()
        for d in devs:
            self.device_combo.addItem(d.label, d)
        default = self._preferred_index(devs)
        if devs:
            self.device_combo.setCurrentIndex(default)
        self.toggle_button.setEnabled(bool(devs))
        self.error_label.setText("" if devs else "No audio devices found")

    def _preferred_index(self, devs: list[AudioDevice]) -> int:
        """Pre-select: the default device of the configured kind, else any device of that kind,
        else any default device, else the first."""
        kind = self._default_kind
        rules = [
            lambda d: kind is not None and d.kind is kind and d.is_default,
            lambda d: kind is not None and d.kind is kind,
            lambda d: d.is_default,
        ]
        for rule in rules:
            for i, d in enumerate(devs):
                if rule(d):
                    return i
        return 0

    def check_health(self) -> None:
        """Stop and tell the user if the capture device died mid-recording."""
        if self._capture is None or not self._capture.recording:
            return
        reason = self._capture.health()
        if reason is not None:
            log.warning("Capture stopped by watchdog: %s", reason)
            self.stop()
            self.error_label.setText(reason + " Pick a device and press Start capture.")
            self.capture_failed.emit(reason)

    def toggle(self) -> None:
        if self.recording:
            self.stop()
        else:
            self.start()

    def start(self) -> None:
        device = self.device_combo.currentData()
        if device is None:
            return
        if self.before_start is not None and not self.before_start():
            self.error_label.setText("Capture not started.")
            return
        # Callbacks run on the audio thread; signals marshal them onto the UI thread.
        capture = self._factory(
            device,
            on_level=self.level_changed.emit,
            on_speech_state=self.speech_changed.emit,
            on_segment=self.segment_ready.emit,
            on_partial=self.partial_ready.emit,
        )
        try:
            capture.start()
        except Exception as e:
            log.exception("Failed to start capture")
            self.error_label.setText(f"Could not start capture: {e}")
            return
        self._capture = capture
        self.error_label.setText("")
        self.recording_label.setText("● RECORDING")
        self.recording_label.setStyleSheet("color: #d32f2f; font-weight: bold;")
        self.toggle_button.setText("Stop capture")
        self.device_combo.setEnabled(False)
        self.refresh_button.setEnabled(False)
        self.recording_changed.emit(True)

    def stop(self) -> None:
        was_recording = self._capture is not None
        if self._capture is not None:
            self._capture.stop()
        self._capture = None
        self.recording_label.setText("Not recording")
        self.recording_label.setStyleSheet("")
        self.toggle_button.setText("Start capture")
        self.device_combo.setEnabled(True)
        self.refresh_button.setEnabled(True)
        self.level_bar.setValue(0)
        self.speech_label.setText("Speech: no")
        if was_recording:
            self.recording_changed.emit(False)

    def _on_level(self, level: float) -> None:
        # RMS of speech is small; scale so normal speech fills a good part of the bar.
        self.level_bar.setValue(min(100, int(level * 400)))

    def _on_speech(self, active: bool) -> None:
        self.speech_label.setText("Speech: YES" if active else "Speech: no")

    def _on_segment(self, seg: np.ndarray) -> None:
        self._segments += 1
        self.segments_label.setText(f"Segments: {self._segments} (last {len(seg) / 16000:.1f}s)")

    def closeEvent(self, event) -> None:  # noqa: N802
        self.stop()
        super().closeEvent(event)
