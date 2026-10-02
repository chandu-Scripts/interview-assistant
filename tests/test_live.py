"""Live end-to-end tests: REAL Whisper + REAL AI providers, driven by synthesized speech that is fed
straight into the audio pipeline (no speakers or microphone involved).

Opt-in, because they spend a few tenths of a cent and need keys and the Whisper model:
    RUN_LIVE=1 python -m pytest tests/test_live.py -s"""

import os
import subprocess
import sys
import time
import wave

import numpy as np
import pytest

from interview_assistant.audio.convert import to_mono_16k
from interview_assistant.config.settings import Settings
from interview_assistant.llm.factory import build_providers
from interview_assistant.llm.router_factory import build_router
from interview_assistant.stt.local_whisper import FasterWhisperProvider
from tests.test_audio import silence
from tests.test_e2e import make_stack  # noqa: F401  (fixture)

pytestmark = pytest.mark.skipif(
    not os.environ.get("RUN_LIVE") or sys.platform != "win32",
    reason="live test: set RUN_LIVE=1 (Windows; needs API keys and the Whisper model)",
)


def synthesize(text: str, tmp_path) -> np.ndarray:
    """Speak `text` with Windows' built-in voice into a wav file and return 16 kHz mono PCM."""
    path = tmp_path / "speech.wav"
    ps = (
        "Add-Type -AssemblyName System.Speech;"
        "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer;"
        f"$s.SetOutputToWaveFile('{path}'); $s.Speak('{text}'); $s.Dispose()"
    )
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True, timeout=60)
    with wave.open(str(path)) as w:
        return to_mono_16k(w.readframes(w.getnframes()), w.getnchannels(), w.getframerate())


def test_live_spoken_question_end_to_end(make_stack, tmp_path):  # noqa: F811
    settings = Settings()
    providers = build_providers(settings)
    if not providers:
        pytest.skip("no API key configured")
    question = "Explain the difference between a process and a thread. Why do threads share memory?"
    speech = synthesize(question, tmp_path)

    stt = FasterWhisperProvider(settings.whisper_model, initial_prompt=settings.stt_vocabulary)
    stt.warmup()
    s = make_stack(
        providers,
        stt=stt,
        router_builder=lambda cb: build_router(settings, providers, on_attempt=cb),
    )
    t0 = time.time()
    s.src.push(np.concatenate([silence(0.3), speech, silence(1.5)]))
    s.qtbot.waitUntil(lambda: len(s.turns()) >= 1, timeout=120_000)
    elapsed = time.time() - t0
    (turn,) = s.turns()

    heard = turn.question.lower()
    assert "process" in heard and "thread" in heard  # real Whisper understood the speech
    assert turn.status == "complete" and turn.category == "concept"
    assert len(turn.answer) > 80 and turn.route.split(":")[0] in providers
    assert turn.cost_usd and 0 < turn.cost_usd < 0.05
    assert s.overlay.answer_text.strip() == turn.answer
    lag = s.overlay.chip.toolTip()
    print(f"\nheard: {turn.question!r}\nroute: {turn.route}  cost: ${turn.cost_usd:.5f}")
    print(f"pipeline (speech pushed -> answer saved): {elapsed:.1f}s\n{lag}")
