import time

import httpx
import numpy as np
import pytest

from interview_assistant.audio.capture import AudioCapture
from interview_assistant.config.settings import Settings
from interview_assistant.stt.base import STTError, TranscriptEvent, pcm_to_wav
from interview_assistant.stt.factory import build_provider
from interview_assistant.stt.openai_stt import OpenAISTTProvider
from interview_assistant.stt.streaming import StreamingTranscriber
from interview_assistant.ui.audio_panel import AudioPanel
from interview_assistant.ui.main_window import MainWindow
from interview_assistant.ui.transcript_panel import TranscriptPanel
from tests.test_audio import DEVS, FakeSource, silence, tone


class FakeSTT:
    name = "fake"

    def __init__(self, delay=0.0, fail=False):
        self.calls: list[int] = []
        self.delay, self.fail = delay, fail

    def warmup(self):
        pass

    def transcribe(self, pcm):
        self.calls.append(len(pcm))
        time.sleep(self.delay)
        if self.fail:
            raise STTError("boom")
        return f"text{len(pcm) // 16000}s"


def wait_idle(t, timeout=3):
    end = time.time() + timeout
    while time.time() < end and not t.idle():
        time.sleep(0.01)
    assert t.idle()


def test_partial_then_final_ordering():
    events: list[TranscriptEvent] = []
    t = StreamingTranscriber(FakeSTT(), events.append)
    t.start()
    t.speech_started()
    t.submit_partial(tone(1.5))
    wait_idle(t)
    t.submit_final(tone(3))
    wait_idle(t)
    t.stop()
    assert [(e.utterance_id, e.is_final, e.text) for e in events] == [
        (1, False, "text1s"),
        (1, True, "text3s"),
    ]


def test_stale_partial_dropped_when_final_arrives():
    events = []
    stt = FakeSTT(delay=0.3)
    t = StreamingTranscriber(stt, events.append)
    t.start()
    t.speech_started()
    t.submit_partial(tone(1))  # starts running (slow)
    time.sleep(0.05)
    t.submit_partial(tone(2))  # queued, then superseded by final
    t.submit_final(tone(3))
    wait_idle(t)
    t.stop()
    assert stt.calls == [16000, 48000]  # the queued partial never ran
    assert events[-1].is_final and events[-1].text == "text3s"
    assert not any(not e.is_final and e.text == "text2s" for e in events)


def test_only_latest_partial_runs():
    stt = FakeSTT(delay=0.2)
    t = StreamingTranscriber(stt, lambda e: None)
    t.start()
    t.speech_started()
    t.submit_partial(tone(1))
    time.sleep(0.05)
    t.submit_partial(tone(2))
    t.submit_partial(tone(3))
    wait_idle(t)
    t.stop()
    assert stt.calls == [16000, 48000]


def test_error_reported_and_worker_survives():
    errs, events = [], []
    stt = FakeSTT(fail=True)
    t = StreamingTranscriber(stt, events.append, errs.append)
    t.start()
    t.speech_started()
    t.submit_final(tone(1))
    wait_idle(t)
    stt.fail = False
    t.speech_started()
    t.submit_final(tone(2))
    wait_idle(t)
    t.stop()
    assert len(errs) == 1 and isinstance(errs[0], STTError)
    assert events[-1].text == "text2s"


def test_stop_drains_finals():
    events = []
    t = StreamingTranscriber(FakeSTT(delay=0.1), events.append)
    t.start()
    for _ in range(3):
        t.speech_started()
        t.submit_final(tone(1))
    t.stop()
    assert len([e for e in events if e.is_final]) == 3


# ---- OpenAI provider (mock transport, no network) ----
def test_openai_provider_request_and_parse():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["auth"] = req.headers["authorization"]
        seen["body"] = req.content
        return httpx.Response(200, json={"text": " hello world "})

    p = OpenAISTTProvider("sk-test", client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert p.transcribe(tone(1)) == "hello world"
    assert seen["auth"] == "Bearer sk-test"
    assert b"gpt-4o-mini-transcribe" in seen["body"] and b"RIFF" in seen["body"]


def test_openai_provider_errors_do_not_leak_key():
    p = OpenAISTTProvider(
        "sk-secret",
        client=httpx.Client(
            transport=httpx.MockTransport(lambda r: httpx.Response(401, text="no"))
        ),
    )
    with pytest.raises(STTError) as ei:
        p.transcribe(tone(1))
    assert "401" in str(ei.value) and "sk-secret" not in str(ei.value)


def test_wav_encoding():
    wav = pcm_to_wav(tone(0.5))
    assert wav[:4] == b"RIFF" and len(wav) == 44 + 16000


def test_factory():
    with pytest.raises(ValueError):
        build_provider(Settings(_env_file=None, stt_provider="openai"))
    assert build_provider(Settings(_env_file=None)).name == "local-whisper"
    s = Settings(_env_file=None, stt_provider="openai", openai_api_key="sk-x")
    assert build_provider(s).name == "openai"


# ---- VAD partials + UI end to end with fake source and fake STT ----
def test_capture_emits_partials():
    src, parts = FakeSource(), []
    cap = AudioCapture(src, 2, 48000, on_partial=parts.append)
    cap.start()
    src.push(np.concatenate([silence(0.3), tone(4), silence(1)]))
    cap.stop()
    assert len(parts) >= 2 and len(parts[1]) > len(parts[0])


def test_transcript_panel_render(qtbot):
    p = TranscriptPanel()
    qtbot.addWidget(p)
    p.apply(TranscriptEvent(1, "hel", False, 1))
    assert "hel" in p.view.toPlainText()
    p.apply(TranscriptEvent(1, "hello there", True, 2))
    p.apply(TranscriptEvent(2, "next <b>", False, 1))
    text = p.view.toPlainText()
    assert "hello there" in text and "next <b>" in text and "hel\n" not in text


def test_full_ui_pipeline(qtbot):
    src = FakeSource()
    panel = AudioPanel(DEVS, lambda d, **kw: AudioCapture(src, d.channels, d.sample_rate, **kw))
    win = MainWindow(panel, FakeSTT())
    qtbot.addWidget(win)
    panel.toggle_button.click()
    src.push(np.concatenate([silence(0.3), tone(3), silence(1)]))
    qtbot.waitUntil(lambda: bool(win.transcript_panel.final_texts), timeout=3000)
    assert win.transcript_panel.final_texts[0].startswith("text")
    win.close()
