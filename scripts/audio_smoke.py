"""Live hardware smoke test: plays a tone and records it back through WASAPI loopback,
then opens the default microphone briefly. Run: python scripts/audio_smoke.py"""

import threading
import time
import winsound

from interview_assistant.audio.capture import AudioCapture
from interview_assistant.audio.devices import default_device, list_devices
from interview_assistant.audio.types import DeviceKind


def run(kind: DeviceKind, seconds: float, play: bool) -> tuple[int, float]:
    dev = default_device(kind, list_devices())
    assert dev, f"no {kind} device"
    print(f"{dev.label} ({dev.channels}ch @ {dev.sample_rate})")
    segs: list = []
    peak = [0.0]
    cap = AudioCapture.for_device(
        dev,
        on_segment=segs.append,
        on_level=lambda lv: peak.__setitem__(0, max(peak[0], lv)),
    )
    cap.start()
    if play:
        threading.Thread(target=winsound.Beep, args=(880, 1500), daemon=True).start()
    time.sleep(seconds)
    cap.stop()
    return len(segs), peak[0]


n, peak = run(DeviceKind.LOOPBACK, 3.5, play=True)
print(f"loopback: segments={n} peak_level={peak:.3f}")
n, peak = run(DeviceKind.MIC, 2.0, play=False)
print(f"mic: segments={n} peak_level={peak:.4f} (ambient)")
