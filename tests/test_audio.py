import numpy as np
import pytest

from interview_assistant.audio.capture import AudioCapture
from interview_assistant.audio.convert import rms_level, to_mono_16k
from interview_assistant.audio.devices import default_device, list_devices
from interview_assistant.audio.types import AudioDevice, DeviceKind
from interview_assistant.audio.vad import SpeechSegmenter, VadConfig
from interview_assistant.ui.audio_panel import AudioPanel


def tone(seconds: float, amp: float = 0.3, rate: int = 16000) -> np.ndarray:
    t = np.arange(int(seconds * rate)) / rate
    return (np.sin(2 * np.pi * 440 * t) * amp * 32767).astype(np.int16)


def silence(seconds: float, rate: int = 16000) -> np.ndarray:
    return np.zeros(int(seconds * rate), dtype=np.int16)


# ---- conversion ----
def test_stereo_48k_to_mono_16k():
    stereo = np.repeat(tone(1.0, rate=48000), 2)  # L == R
    out = to_mono_16k(stereo.tobytes(), channels=2, rate=48000)
    assert out.dtype == np.int16
    assert abs(len(out) - 16000) <= 1
    assert rms_level(out) == pytest.approx(0.3 / np.sqrt(2), rel=0.1)


def test_non_integer_ratio_resample():
    out = to_mono_16k(tone(1.0, rate=44100).tobytes(), channels=1, rate=44100)
    assert abs(len(out) - 16000) <= 1


def test_empty_input():
    assert len(to_mono_16k(b"", 2, 48000)) == 0
    assert rms_level(np.empty(0, np.int16)) == 0.0


# ---- VAD ----
def run_vad(audio: np.ndarray, cfg: VadConfig | None = None):
    segs, states = [], []
    v = SpeechSegmenter(segs.append, cfg, states.append)
    for i in range(0, len(audio), 1000):  # odd chunk size on purpose
        v.feed(audio[i : i + 1000])
    return v, segs, states


def test_vad_detects_one_segment_with_preroll():
    audio = np.concatenate([silence(1), tone(1.0), silence(1.5)])
    _, segs, states = run_vad(audio)
    assert len(segs) == 1
    dur = len(segs[0]) / 16000
    assert 1.2 < dur < 2.2  # speech + pre-roll + trailing silence
    assert states == [True, False]


def test_vad_ignores_silence_and_short_blips():
    _, segs, _ = run_vad(np.concatenate([silence(1), tone(0.03), silence(2)]))
    assert segs == []


def test_vad_splits_two_utterances():
    audio = np.concatenate([tone(1), silence(1.5), tone(1), silence(1.5)])
    _, segs, _ = run_vad(audio)
    assert len(segs) == 2


def test_vad_flush_closes_open_segment():
    v, segs, _ = run_vad(np.concatenate([silence(0.5), tone(1.0)]))
    assert v.active and segs == []
    v.flush()
    assert len(segs) == 1 and not v.active


def test_vad_max_segment_forced_close():
    cfg = VadConfig(max_segment_s=2.0)
    _, segs, _ = run_vad(tone(5.0), cfg)
    assert len(segs) >= 2


# ---- capture with fake source ----
class FakeSource:
    def __init__(self):
        self.cb = None
        self.started = self.stopped = 0

    def start(self, on_raw):
        self.cb = on_raw
        self.started += 1

    def stop(self):
        self.stopped += 1

    def push(self, pcm16k: np.ndarray):
        stereo = np.repeat(np.repeat(pcm16k, 3), 2)  # fake 48 kHz stereo device
        for i in range(0, len(stereo), 2048):
            self.cb(stereo[i : i + 2048].tobytes())


def test_capture_pipeline_and_explicit_start_stop():
    src, segs, levels = FakeSource(), [], []
    cap = AudioCapture(src, 2, 48000, on_segment=segs.append, on_level=levels.append)
    assert not cap.recording and src.started == 0
    cap.start()
    cap.start()  # idempotent
    assert cap.recording and src.started == 1
    src.push(np.concatenate([silence(0.5), tone(1), silence(1)]))
    cap.stop()
    assert not cap.recording and src.stopped == 1
    assert len(segs) == 1 and max(levels) > 0.1
    src.cb(tone(1).tobytes())  # data after stop is ignored
    assert len(segs) == 1


def test_capture_start_failure_resets_state():
    class Bad(FakeSource):
        def start(self, on_raw):
            raise OSError("no device")

    cap = AudioCapture(Bad(), 1, 16000)
    with pytest.raises(OSError):
        cap.start()
    assert not cap.recording


# ---- devices (real hardware, tolerant) ----
def test_list_devices_real():
    devs = list_devices()
    assert all(isinstance(d, AudioDevice) for d in devs)
    d = default_device(DeviceKind.LOOPBACK, devs)
    assert d is None or d.kind is DeviceKind.LOOPBACK


# ---- UI ----
DEVS = [
    AudioDevice(1, "Mic A", DeviceKind.MIC, 2, 48000, True),
    AudioDevice(2, "Speakers [Loopback]", DeviceKind.LOOPBACK, 2, 48000, True),
]


def test_panel_flow(qtbot):
    src = FakeSource()

    def factory(device, **kw):
        return AudioCapture(src, device.channels, device.sample_rate, **kw)

    panel = AudioPanel(DEVS, factory)
    qtbot.addWidget(panel)
    assert panel.device_combo.count() == 2
    assert not panel.recording
    assert panel.recording_label.text() == "Not recording"

    panel.device_combo.setCurrentIndex(1)
    panel.toggle_button.click()
    assert panel.recording and "RECORDING" in panel.recording_label.text()
    assert not panel.device_combo.isEnabled()

    src.push(np.concatenate([silence(0.3), tone(1), silence(1)]))
    qtbot.waitUntil(lambda: "Segments: 1" in panel.segments_label.text(), timeout=2000)

    panel.toggle_button.click()
    assert not panel.recording and panel.recording_label.text() == "Not recording"
    assert panel.device_combo.isEnabled()


def test_panel_start_error_shown(qtbot):
    class Bad(FakeSource):
        def start(self, on_raw):
            raise OSError("device busy")

    panel = AudioPanel(DEVS, lambda d, **kw: AudioCapture(Bad(), 2, 48000, **kw))
    qtbot.addWidget(panel)
    panel.toggle_button.click()
    assert not panel.recording and "device busy" in panel.error_label.text()


def test_panel_no_devices(qtbot):
    panel = AudioPanel([], lambda d, **kw: None)
    qtbot.addWidget(panel)
    assert not panel.toggle_button.isEnabled()


def test_panel_restrict_kind_filters_the_combo(qtbot):
    """Step 19: a fixed-role panel (e.g. "Microphone" alongside a separate "System audio" one)
    only ever lists devices of its own kind, regardless of what's passed in or found."""
    panel = AudioPanel(DEVS, lambda d, **kw: None, restrict_kind=DeviceKind.MIC)
    qtbot.addWidget(panel)
    assert panel.device_combo.count() == 1
    assert panel.device_combo.itemData(0).kind is DeviceKind.MIC

    panel2 = AudioPanel(DEVS, lambda d, **kw: None, restrict_kind=DeviceKind.LOOPBACK)
    qtbot.addWidget(panel2)
    assert panel2.device_combo.count() == 1
    assert panel2.device_combo.itemData(0).kind is DeviceKind.LOOPBACK


def test_panel_restrict_kind_none_is_unrestricted_like_before(qtbot):
    panel = AudioPanel(DEVS, lambda d, **kw: None)
    qtbot.addWidget(panel)
    assert panel.device_combo.count() == len(DEVS)
