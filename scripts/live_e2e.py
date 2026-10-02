"""Live end-to-end: play a wav on the speakers, capture via loopback, stream transcripts.
Usage: python scripts/live_e2e.py file.wav"""

import sys
import threading
import time
import winsound

from interview_assistant.audio.capture import AudioCapture
from interview_assistant.audio.devices import default_device, list_devices
from interview_assistant.audio.types import DeviceKind
from interview_assistant.stt.local_whisper import FasterWhisperProvider
from interview_assistant.stt.streaming import StreamingTranscriber

t0 = time.time()
provider = FasterWhisperProvider()
provider.warmup()
tr = StreamingTranscriber(
    provider,
    lambda e: print(
        f"[{time.time() - t0:5.1f}s] {'FINAL  ' if e.is_final else 'partial'} "
        f"utt{e.utterance_id}: {e.text}"
    ),
    lambda e: print("ERROR", e),
)
tr.start()
dev = default_device(DeviceKind.LOOPBACK, list_devices())
assert dev
cap = AudioCapture.for_device(
    dev,
    on_speech_state=lambda on: tr.speech_started() if on else None,
    on_partial=tr.submit_partial,
    on_segment=tr.submit_final,
)
cap.start()
time.sleep(0.5)
threading.Thread(target=winsound.PlaySound, args=(sys.argv[1], winsound.SND_FILENAME)).start()
time.sleep(10)
cap.stop()
while not tr.idle():
    time.sleep(0.1)
tr.stop()
