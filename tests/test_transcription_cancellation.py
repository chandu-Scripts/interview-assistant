"""Capture epochs fence audio, provider work and queued Qt completions independently."""

import threading

import pytest

from interview_assistant.app.controller import AssistantController
from interview_assistant.app.presentation import PresentationMode
from interview_assistant.audio.types import DeviceKind
from interview_assistant.stt.base import TranscriptEvent
from interview_assistant.stt.streaming import StreamingTranscriber
from interview_assistant.ui.audio_panel import AudioPanel
from interview_assistant.ui.main_window import MainWindow
from tests.test_app import FakeJev
from tests.test_audio import DEVS, tone
from tests.test_stt import FakeSTT, wait_idle


class HeldProvider(FakeSTT):
    def __init__(self, *, fail=False):
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()
        self.fail_first = fail

    def transcribe(self, pcm):
        self.calls.append(len(pcm))
        if len(self.calls) == 1:
            self.entered.set()
            assert self.release.wait(5)
            if self.fail_first:
                raise RuntimeError("old capture failed")
            return "old question"
        return "new question"


class FailedWarmup(FakeSTT):
    def __init__(self):
        super().__init__()
        self.entered, self.release = threading.Event(), threading.Event()

    def warmup(self):
        self.entered.set()
        assert self.release.wait(5)
        raise RuntimeError("Synthetic private warmup detail")


@pytest.mark.parametrize("resume_before_failure", [False, True])
@pytest.mark.parametrize("source", [DeviceKind.MIC, DeviceKind.LOOPBACK])
def test_cancelled_warmup_failure_notifies_only_the_new_capture(source, resume_before_failure):
    preview, events, errors = FailedWarmup(), [], []
    transcriber = StreamingTranscriber(
        FakeSTT(), events.append, errors.append, preview_provider=preview, source=source
    )
    transcriber.start()
    try:
        assert preview.entered.wait(2)
        transcriber.speech_started()
        transcriber.submit_partial(tone(1))
        transcriber.cancel()
        if resume_before_failure:
            transcriber.speech_started()
        preview.release.set()
        transcriber._preview._thread.join(2)
        assert not transcriber._preview._thread.is_alive()
        assert events == [] and errors == []
        if not resume_before_failure:
            transcriber.speech_started()
        transcriber.submit_partial(tone(2))
        transcriber.submit_partial(tone(2))
        assert transcriber._preview_failed
        assert len(errors) == 1
        assert str(errors[0]) == (
            "Live transcript preview unavailable; final transcription still works."
        )
        transcriber.submit_final(tone(2))
        wait_idle(transcriber)
        assert len(events) == 1 and events[0].is_final and events[0].source == source
        assert preview.calls == []
        transcriber.speech_started()
        assert len(errors) == 1  # No repeated notice for each utterance in one capture.
        transcriber.cancel()
        transcriber.speech_started()
        assert len(errors) == 2  # A fresh capture still explains the degraded preview.
    finally:
        preview.release.set()
        transcriber.stop()


def test_permanent_stop_keeps_failed_preview_warmup_silent():
    preview, events, errors = FailedWarmup(), [], []
    transcriber = StreamingTranscriber(
        FakeSTT(), events.append, errors.append, preview_provider=preview
    )
    transcriber.start()
    try:
        assert preview.entered.wait(2)
        transcriber.stop(timeout=0)
        preview.release.set()
        transcriber._preview._thread.join(2)
        transcriber.speech_started()
        transcriber.submit_partial(tone(1))
        transcriber.submit_final(tone(1))
        assert not errors and not events
    finally:
        preview.release.set()
        transcriber.stop()


@pytest.mark.parametrize("fail", [False, True])
@pytest.mark.parametrize("preview", [False, True])
def test_cancel_fences_running_and_queued_work_and_allows_new_speech(fail, preview):
    held, events, errors = HeldProvider(fail=fail), [], []
    transcriber = StreamingTranscriber(
        FakeSTT() if preview else held,
        events.append,
        errors.append,
        preview_provider=held if preview else None,
    )
    transcriber.start()
    try:
        transcriber.speech_started()
        submit = transcriber.submit_partial if preview else transcriber.submit_final
        submit(tone(1))
        assert held.entered.wait(2)
        transcriber.speech_started()
        submit(tone(2))
        transcriber.cancel()
        held.release.set()
        wait_idle(transcriber)
        assert events == [] and errors == [] and held.calls == [16000]
        transcriber.speech_started()
        submit(tone(3))
        wait_idle(transcriber)
        assert [event.text for event in events] == ["new question"]
    finally:
        held.release.set()
        transcriber.stop()


@pytest.mark.parametrize("fail", [False, True])
def test_stop_suppresses_inflight_final_and_error(fail):
    held, events, errors = HeldProvider(fail=fail), [], []
    transcriber = StreamingTranscriber(held, events.append, errors.append)
    transcriber.start()
    try:
        transcriber.speech_started()
        transcriber.submit_final(tone(1))
        assert held.entered.wait(2)
        transcriber.stop(timeout=0)
        transcriber.speech_started()
        transcriber.submit_final(tone(2))
        held.release.set()
        wait_idle(transcriber)
        assert events == [] and errors == [] and held.calls == [16000]
    finally:
        held.release.set()
        transcriber.stop()


class FakeCapture:
    def __init__(self, **callbacks):
        self.callbacks = callbacks
        self.recording = False

    def start(self):
        self.recording = True

    def stop(self):
        self.recording = False

    def health(self):
        return None

    def question(self, seconds=1):
        self.callbacks["on_speech_state"](True)
        self.callbacks["on_speech_state"](False)
        self.callbacks["on_segment"](tone(seconds))


def make_panel():
    captures = []

    def factory(_device, **callbacks):
        capture = FakeCapture(**callbacks)
        captures.append(capture)
        return capture

    return AudioPanel(DEVS, capture_factory=factory), captures


def make_window(qtbot, source, provider):
    primary, primary_captures = make_panel()
    mic, mic_captures = make_panel()
    jev = FakeJev()
    controller = AssistantController(jev, answer_mode="auto", poll_ms=10)
    window = MainWindow(
        primary,
        provider if source == DeviceKind.LOOPBACK else FakeSTT(),
        controller,
        audio_source="loopback",
        mic_panel=mic,
        mic_provider=provider if source == DeviceKind.MIC else FakeSTT(),
    )
    qtbot.addWidget(window)
    panel = mic if source == DeviceKind.MIC else primary
    captures = mic_captures if source == DeviceKind.MIC else primary_captures
    transcriber = window.mic_transcriber if source == DeviceKind.MIC else window.transcriber
    return window, panel, captures, transcriber, controller, jev


@pytest.mark.parametrize("source", [DeviceKind.MIC, DeviceKind.LOOPBACK])
@pytest.mark.parametrize("presentation", [False, True])
@pytest.mark.parametrize("fail", [False, True])
def test_stopped_capture_cannot_answer_after_resume(qtbot, source, presentation, fail):
    provider = HeldProvider(fail=fail)
    window, panel, captures, transcriber, controller, jev = make_window(qtbot, source, provider)
    try:
        panel.start()
        captures[-1].question()
        qtbot.waitUntil(provider.entered.is_set)
        if presentation:
            mode = PresentationMode([], None, [window.audio_panel, window.mic_panel], controller)
            mode.activate()
            mode.deactivate()
        else:
            panel.stop()
        assert not panel.recording
        # Resume BEFORE the old result returns: a recording flag alone cannot protect this.
        panel.start()
        provider.release.set()
        qtbot.waitUntil(transcriber.idle)
        qtbot.wait(30)
        assert window.transcript_panel.final_texts == [] and jev.asked == []
        assert window.transcript_panel.status_label.text() == ""
        captures[-1].question(2)
        qtbot.waitUntil(lambda: jev.asked == ["new question"], timeout=3000)
        assert window.transcript_panel.final_texts == ["new question"]
        assert controller._agg.in_flight == 0
    finally:
        provider.release.set()
        window.close()


@pytest.mark.parametrize("source", [DeviceKind.MIC, DeviceKind.LOOPBACK])
@pytest.mark.parametrize("fail", [False, True])
def test_already_queued_worker_delivery_is_rejected_after_restart(qtbot, source, fail):
    provider = FakeSTT(fail=fail)
    window, panel, captures, transcriber, controller, jev = make_window(qtbot, source, provider)
    try:
        panel.start()
        panel.speech_changed.emit(True)
        panel.speech_changed.emit(False)
        panel.segment_ready.emit(tone(1))
        # Do not process Qt here: the provider completes and queues its delivery.
        wait_idle(transcriber)
        assert provider.calls == [16000]
        panel.stop()
        panel.start()
        qtbot.wait(30)
        assert window.transcript_panel.final_texts == [] and jev.asked == []
        assert window.transcript_panel.status_label.text() == ""
        assert controller._agg.in_flight == 0
        provider.fail = False
        captures[-1].question(2)
        qtbot.waitUntil(lambda: jev.asked == ["text2s"], timeout=3000)
    finally:
        window.close()


@pytest.mark.parametrize("source", [DeviceKind.MIC, DeviceKind.LOOPBACK])
def test_old_capture_callbacks_queued_before_or_after_restart_are_rejected(qtbot, source):
    provider = FakeSTT()
    window, panel, captures, _transcriber, controller, jev = make_window(qtbot, source, provider)
    try:
        panel.start()
        old_capture = captures[-1]
        old_capture.question()
        panel.stop()
        panel.start()
        old_capture.question()  # A late callback from the previous physical capture.
        qtbot.wait(30)
        assert provider.calls == [] and jev.asked == []
        assert controller._agg.in_flight == 0
        captures[-1].question(2)
        qtbot.waitUntil(lambda: jev.asked == ["text2s"], timeout=3000)
    finally:
        window.close()


@pytest.mark.parametrize("source", [DeviceKind.MIC, DeviceKind.LOOPBACK])
def test_stopping_one_source_preserves_other_source_and_completed_transcripts(qtbot, source):
    held = HeldProvider()
    window, panel, captures, transcriber, _controller, jev = make_window(qtbot, source, held)
    other = window.audio_panel if source == DeviceKind.MIC else window.mic_panel
    other_source = DeviceKind.LOOPBACK if source == DeviceKind.MIC else DeviceKind.MIC
    try:
        panel.start()
        other.start()
        window.transcript_panel.apply(TranscriptEvent(100, "completed", True, 1, source))
        window.transcript_panel.apply(TranscriptEvent(101, "unfinished", False, 1, source))
        window.transcript_panel.apply(TranscriptEvent(101, "other preview", False, 1, other_source))
        captures[-1].question()
        qtbot.waitUntil(held.entered.is_set)
        panel.stop()
        assert "unfinished" not in window.transcript_panel.view.toPlainText()
        assert "other preview" in window.transcript_panel.view.toPlainText()
        held.release.set()
        qtbot.waitUntil(transcriber.idle)
        other.speech_changed.emit(True)
        other.speech_changed.emit(False)
        other.segment_ready.emit(tone(2))
        qtbot.waitUntil(lambda: jev.asked == ["text2s"], timeout=3000)
        assert window.transcript_panel.final_texts == ["completed", "text2s"]
    finally:
        held.release.set()
        window.close()
