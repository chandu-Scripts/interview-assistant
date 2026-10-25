import threading
import time

import pytest

from interview_assistant.app.aggregator import UtteranceAggregator
from interview_assistant.audio.types import DeviceKind
from interview_assistant.audio.vad import SpeechSegmenter
from interview_assistant.config.settings import Settings
from interview_assistant.stt.factory import build_preview_provider
from interview_assistant.stt.streaming import StreamingTranscriber
from tests.test_audio import tone
from tests.test_stt import FakeSTT, wait_idle


def test_previews_arrive_during_continuous_speech():
    previews, finals = [], []
    vad = SpeechSegmenter(finals.append, on_partial=previews.append)
    vad.feed(tone(1))
    assert len(previews) == 1 and finals == [] and vad.active
    vad.feed(tone(1))
    assert len(previews) == 2 and len(previews[1]) > len(previews[0])
    assert finals == []


@pytest.mark.parametrize("source", [DeviceKind.MIC, DeviceKind.LOOPBACK])
def test_local_preview_does_not_upload_cloud_audio(source):
    cloud, local, events = FakeSTT(), FakeSTT(), []
    transcriber = StreamingTranscriber(cloud, events.append, source=source, preview_provider=local)
    transcriber.start()
    try:
        transcriber.speech_started()
        for seconds in (1, 2):
            transcriber.submit_partial(tone(seconds))
            wait_idle(transcriber)
        assert cloud.calls == []
        assert [e.text for e in events] == ["text1s", "text2s"]
        assert all(not e.is_final and e.source == source for e in events)
        transcriber.submit_final(tone(3))
        wait_idle(transcriber)
        assert cloud.calls == [48000]
        assert events[-1].is_final and events[-1].text == "text3s"
    finally:
        transcriber.stop()


@pytest.mark.parametrize("action", ["final", "new_utterance", "stop"])
def test_slow_preview_cannot_replace_final_or_new_speech(action):
    entered, release = threading.Event(), threading.Event()

    class SlowPreview(FakeSTT):
        def transcribe(self, pcm):
            entered.set()
            assert release.wait(5)
            return "stale preview"

    events = []
    transcriber = StreamingTranscriber(FakeSTT(), events.append, preview_provider=SlowPreview())
    transcriber.start()
    try:
        transcriber.speech_started()
        transcriber.submit_partial(tone(1))
        assert entered.wait(2)
        if action == "final":
            transcriber.submit_final(tone(2))
            # A slow preview must not delay final transcription.
            deadline = time.monotonic() + 2
            while not events and time.monotonic() < deadline:
                time.sleep(0.01)
            assert events and events[-1].is_final
        elif action == "new_utterance":
            transcriber.speech_started()
        else:
            transcriber.stop(timeout=0)
        release.set()
        wait_idle(transcriber)
        assert all(e.text != "stale preview" for e in events)
    finally:
        release.set()
        transcriber.stop()


@pytest.mark.parametrize("language,model", [("en", "tiny.en"), ("es", "tiny")])
def test_preview_model_is_lightweight_and_keeps_final_model_setting(language, model):
    settings = Settings(stt_language=language, whisper_model="base.en")
    provider = build_preview_provider(settings)
    assert provider.model == model
    assert provider._cpu_threads == 2
    assert settings.whisper_model == "base.en"


def test_preview_warmup_failure_does_not_strand_queued_audio():
    entered, release = threading.Event(), threading.Event()

    class FailedWarmup(FakeSTT):
        def warmup(self):
            entered.set()
            assert release.wait(5)
            raise RuntimeError("Model unavailable")

    events, errors = [], []
    transcriber = StreamingTranscriber(
        FakeSTT(), events.append, errors.append, preview_provider=FailedWarmup()
    )
    transcriber.start()
    try:
        assert entered.wait(2)
        transcriber.speech_started()
        transcriber.submit_partial(tone(1))
        release.set()
        transcriber.submit_final(tone(2))
        wait_idle(transcriber)
        assert len(errors) == 1
        assert events[-1].is_final
    finally:
        release.set()
        transcriber.stop()


def test_preview_failure_warns_once_and_final_still_works():
    cloud, events, errors = FakeSTT(), [], []
    transcriber = StreamingTranscriber(
        cloud, events.append, errors.append, preview_provider=FakeSTT(fail=True)
    )
    transcriber.start()
    try:
        transcriber.speech_started()
        transcriber.submit_partial(tone(1))
        wait_idle(transcriber)
        transcriber.submit_partial(tone(2))
        transcriber.submit_final(tone(3))
        wait_idle(transcriber)
        assert len(errors) == 1 and "final transcription still works" in str(errors[0])
        assert cloud.calls == [48000]
        assert events[-1].is_final
    finally:
        transcriber.stop()


def test_final_discards_queued_preview_instead_of_wasting_another_decode():
    entered, release = threading.Event(), threading.Event()

    class HeldPreview(FakeSTT):
        def transcribe(self, pcm):
            self.calls.append(len(pcm))
            entered.set()
            assert release.wait(5)
            return "preview"

    local, events = HeldPreview(), []
    transcriber = StreamingTranscriber(FakeSTT(), events.append, preview_provider=local)
    transcriber.start()
    try:
        transcriber.speech_started()
        transcriber.submit_partial(tone(1))
        assert entered.wait(2)
        transcriber.submit_partial(tone(2))
        transcriber.submit_final(tone(3))
        release.set()
        wait_idle(transcriber)
        assert local.calls == [16000]
        assert len(events) == 1 and events[0].is_final
    finally:
        release.set()
        transcriber.stop()


def test_dropped_backlog_does_not_hold_the_next_question_for_eight_seconds(monkeypatch):
    monkeypatch.setattr("interview_assistant.stt.streaming.MAX_BACKLOG", 2)
    now = [0.0]
    aggregator = UtteranceAggregator(clock=lambda: now[0])
    transcriber = StreamingTranscriber(FakeSTT(), lambda e: aggregator.final(e.text))
    for _ in range(3):
        aggregator.speech_started()
        aggregator.speech_ended()
        transcriber.speech_started()
        transcriber.submit_final(tone(1))
    transcriber.start()
    try:
        wait_idle(transcriber)
        now[0] = 0.3
        assert aggregator.in_flight == 0
        assert aggregator.poll() is not None
    finally:
        transcriber.stop()
