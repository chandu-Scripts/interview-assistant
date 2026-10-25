"""Opt-in network test: stream a chosen synthetic WAV at real-time speed (no microphone).

Usage: python scripts/test_speechmatics_live.py path/to/synthetic.wav
Uses the saved Speechmatics key and a small amount of API credit.
"""

import argparse
import json
import threading
import time
import wave

import numpy as np

from interview_assistant.audio.convert import to_mono_16k
from interview_assistant.audio.vad import SpeechSegmenter, VadConfig
from interview_assistant.config.keys import KeyringStore
from interview_assistant.stt.realtime import RealtimeTranscriber
from interview_assistant.stt.speechmatics import SpeechmaticsProvider


class NoFallback:
    name = "disabled-for-live-check"

    def warmup(self):
        pass

    def transcribe(self, pcm):
        raise RuntimeError("Live check must not hide a Speechmatics failure behind fallback")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wav")
    args = parser.parse_args()
    key = KeyringStore().get("speechmatics")
    if not key:
        raise SystemExit("No Speechmatics credential found")
    with wave.open(args.wav, "rb") as wav:
        if wav.getsampwidth() != 2:
            raise SystemExit("Use a 16-bit PCM WAV")
        pcm = to_mono_16k(wav.readframes(wav.getnframes()), wav.getnchannels(), wav.getframerate())
    events, errors, starts, ends = [], [], [], []
    lock = threading.Lock()

    def on_event(event):
        with lock:
            events.append((time.monotonic(), event))

    worker = RealtimeTranscriber(
        SpeechmaticsProvider(key, NoFallback()),
        on_event,
        lambda error: errors.append(str(error)),
    )

    def state(active):
        if active:
            starts.append(time.monotonic())
            worker.speech_started()
        else:
            ends.append(time.monotonic())

    vad = SpeechSegmenter(
        worker.submit_final,
        VadConfig(partial_interval_ms=120),
        state,
        worker.submit_partial,
    )
    worker.start()
    began = time.monotonic()
    try:
        pcm = np.concatenate([pcm, np.zeros(16000, dtype=np.int16)])
        for offset in range(0, len(pcm), 480):
            vad.feed(pcm[offset : offset + 480])
            time.sleep(max(0, began + (offset + 480) / 16000 - time.monotonic()))
        vad.flush()
        deadline = time.monotonic() + 15
        while not worker.idle() and time.monotonic() < deadline:
            time.sleep(0.02)
        with lock:
            partials = [(at, ev) for at, ev in events if not ev.is_final]
            finals = [(at, ev) for at, ev in events if ev.is_final]
            print(
                json.dumps(
                    {
                        "partials": len(partials),
                        "finals": len(finals),
                        "first_partial_after_speech_started_s": round(partials[0][0] - starts[0], 3)
                        if partials
                        else None,
                        "final_after_vad_end_s": [
                            round(at - ends[ev.utterance_id - 1], 3) for at, ev in finals
                        ],
                        "synthetic_transcript": " ".join(ev.text for _, ev in finals),
                        "errors": errors,
                        "idle": worker.idle(),
                    },
                    indent=2,
                )
            )
        if errors or not partials or not finals or not worker.idle():
            raise SystemExit(1)
    finally:
        worker.stop()


if __name__ == "__main__":
    main()
