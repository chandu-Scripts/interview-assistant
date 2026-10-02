"""Benchmark local Whisper models on this PC with synthesized interview questions.

Usage: python scripts/bench_stt.py [model ...]   (default: tiny.en base.en small.en)
Measures load time, transcription time (real-time factor) and word error rate against the spoken text."""

import re
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path

import numpy as np

from interview_assistant.audio.convert import to_mono_16k

QUESTIONS = [
    "Tell me about a time you resolved a conflict on your team and what the outcome was.",
    "Design a rate limiter for an API. What trade-offs would you consider for a distributed system?",
    "What is the difference between a process and a thread, and why do threads share memory?",
    "Write a SQL query using a window function to find the second highest salary per department.",
    "Explain how garbage collection works in Python and when reference cycles are a problem.",
]
PROMPT = "Software engineering interview. Terms: API, rate limiter, SQL, Kubernetes, Redis, OAuth, latency."


def synth(text: str, path: Path) -> None:
    ps = (
        "Add-Type -AssemblyName System.Speech; $s=New-Object System.Speech.Synthesis.SpeechSynthesizer;"
        f"$s.SetOutputToWaveFile('{path}'); $s.Speak('{text}'); $s.Dispose()"
    )
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True)


def load(path: Path) -> np.ndarray:
    with wave.open(str(path)) as w:
        pcm = to_mono_16k(w.readframes(w.getnframes()), w.getnchannels(), w.getframerate())
    return pcm.astype(np.float32) / 32768.0


def words(s: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", s.lower())


def wer(ref: str, hyp: str) -> float:
    r, h = words(ref), words(hyp)
    d = list(range(len(h) + 1))
    for i in range(1, len(r) + 1):
        prev, d[0] = d[0], i
        for j in range(1, len(h) + 1):
            cur = d[j]
            d[j] = min(d[j] + 1, d[j - 1] + 1, prev + (r[i - 1] != h[j - 1]))
            prev = cur
    return d[len(h)] / max(len(r), 1)


def main() -> None:
    from faster_whisper import WhisperModel

    models = sys.argv[1:] or ["tiny.en", "base.en", "small.en"]
    tmp = Path(tempfile.mkdtemp())
    audio = []
    for i, q in enumerate(QUESTIONS):
        p = tmp / f"q{i}.wav"
        synth(q, p)
        audio.append(load(p))
    total_s = sum(len(a) for a in audio) / 16000  # samples at 16 kHz
    print(f"{len(audio)} questions, {total_s:.0f}s of speech\n")
    print(
        f"{'model':10} {'threads':>7} {'prompt':>6} {'load':>6} {'time':>6} {'RTF':>6} {'WER':>6}"
    )
    for name in models:
        t0 = time.time()
        for threads in (4, 8):
            model = WhisperModel(name, device="cpu", compute_type="int8", cpu_threads=threads)
            load_s = time.time() - t0
            for use_prompt in (False, True):
                model.transcribe(audio[0][:16000], language="en", beam_size=1)  # warm up
                errs, t1 = [], time.time()
                for a, q in zip(audio, QUESTIONS, strict=True):
                    segs, _ = model.transcribe(
                        a,
                        language="en",
                        beam_size=1,
                        vad_filter=True,
                        condition_on_previous_text=False,
                        initial_prompt=PROMPT if use_prompt else None,
                    )
                    errs.append(wer(q, " ".join(s.text for s in segs)))
                dt = time.time() - t1
                print(
                    f"{name:10} {threads:>7} {'yes' if use_prompt else 'no':>6} {load_s:>5.1f}s "
                    f"{dt:>5.1f}s {dt / total_s:>6.2f} {sum(errs) / len(errs):>6.1%}"
                )
            t0 = time.time()


if __name__ == "__main__":
    main()
