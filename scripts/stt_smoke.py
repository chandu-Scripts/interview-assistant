"""Transcribe a wav file with local whisper. Usage: python scripts/stt_smoke.py file.wav"""

import sys
import time
import wave

import numpy as np

from interview_assistant.audio.convert import to_mono_16k
from interview_assistant.stt.local_whisper import FasterWhisperProvider

with wave.open(sys.argv[1]) as w:
    raw = w.readframes(w.getnframes())
    pcm = to_mono_16k(raw, w.getnchannels(), w.getframerate())
p = FasterWhisperProvider()
t0 = time.time()
p.warmup()
print(f"model load {time.time() - t0:.1f}s")
t0 = time.time()
print(repr(p.transcribe(pcm)), f"({len(pcm) / 16000:.1f}s audio in {time.time() - t0:.1f}s)")
print("silence ->", repr(p.transcribe(np.zeros(32000, dtype=np.int16))))
