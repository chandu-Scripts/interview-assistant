import json
import queue
import threading
import time

import numpy as np
import pytest

from interview_assistant.audio.types import DeviceKind
from interview_assistant.config.settings import Settings
from interview_assistant.stt.base import STTError
from interview_assistant.stt.factory import build_provider
from interview_assistant.stt.metered import MeteredSTT
from interview_assistant.stt.realtime import RealtimeTranscriber
from interview_assistant.stt.speechmatics import SpeechmaticsProvider
from interview_assistant.ui.audio_panel import AudioPanel
from interview_assistant.ui.main_window import MainWindow
from tests.test_audio import DEVS, FakeSource, silence, tone
from tests.test_stt import FakeSTT, wait_idle


class Socket:
    def __init__(self, fail=False):
        self.messages = queue.Queue()
        self.audio = bytearray()
        self.config = None
        self.closed = False
        self.sequence = 0
        self.fail = fail

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def send(self, data):
        if isinstance(data, bytes):
            self.audio.extend(data)
            self.sequence += 1
            self.messages.put(
                {"message": "AddPartialTranscript", "metadata": {"transcript": "How many"}}
            )
        else:
            data = json.loads(data)
            if data["message"] == "StartRecognition":
                self.config = data
                self.messages.put({"message": "Info", "type": "concurrent_session_usage"})
                self.messages.put({"message": "Error" if self.fail else "RecognitionStarted"})
            elif data["message"] == "EndOfStream":
                assert data["last_seq_no"] == self.sequence
                for text in ["How many", "catalogs?"]:
                    self.messages.put(
                        {"message": "AddTranscript", "metadata": {"transcript": text}}
                    )
                self.messages.put({"message": "EndOfTranscript"})

    def recv(self, timeout):
        return json.dumps(self.messages.get(timeout=timeout))

    def close(self):
        self.closed = True
        self.messages.put({"message": "Error"})


def provider(socket=None, fallback=None):
    socket = socket or Socket()
    return SpeechmaticsProvider(
        "fake-secret", fallback or FakeSTT(), connector=lambda *a, **k: socket
    )


def wait(predicate):
    deadline = time.monotonic() + 3
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.005)
    assert predicate()


def test_wire_protocol_partial_correction_and_audio_encoding():
    ws = Socket()
    previews = []
    text = provider(ws).stream(iter([tone(0.5), tone(0.3)]), previews.append)
    assert text == "How many catalogs?"
    assert previews[0] == "How many"
    assert previews[-1] == text
    assert len(ws.audio) == 16000 * 2 * 0.8
    assert ws.config["transcription_config"]["enable_partials"]
    assert ws.config["transcription_config"]["max_delay"] == 0.7
    assert ws.config["audio_format"]["encoding"] == "pcm_s16le"
    assert ws.closed


def test_network_errors_are_sanitized_and_transport_logging_disabled(caplog):
    def broken(*args, **kwargs):
        assert kwargs["logger"].disabled
        raise ValueError("fake-secret Authorization header")

    p = SpeechmaticsProvider("fake-secret", FakeSTT(), connector=broken)
    with pytest.raises(STTError) as error:
        p.stream(iter([]), lambda text: None)
    assert "fake-secret" not in str(error.value)
    assert "fake-secret" not in caplog.text
    assert "Speechmatics connection: failure" in caplog.text
    assert error.value.__suppress_context__


def test_streams_before_final_without_resending_prefix_and_meters_once():
    events, usage = [], []
    ws = Socket()
    t = RealtimeTranscriber(
        MeteredSTT(provider(ws), lambda *args: usage.append(args)), events.append
    )
    t.start()
    try:
        assert ws.config is None  # no automatic connection on app startup
        t.speech_started()
        audio = np.arange(8000, dtype=np.int16)
        t.submit_partial(audio[:2000])
        wait(lambda: bool(events))
        assert not any(e.is_final for e in events)
        t.submit_partial(audio[:4000])
        t.submit_final(audio)
        wait_idle(t)
        assert bytes(ws.audio) == audio.tobytes()
        assert sum(e.is_final for e in events) == 1
        assert events[-1].text == "How many catalogs?"
        assert usage == [("speechmatics", "standard", 0.5)]
    finally:
        t.stop()


def test_disconnect_falls_back_on_whole_question_and_counts_correct_provider():
    events, errors, usage = [], [], []
    fallback = FakeSTT()
    t = RealtimeTranscriber(
        MeteredSTT(provider(Socket(fail=True), fallback), lambda *a: usage.append(a)),
        events.append,
        errors.append,
    )
    t.start()
    try:
        t.speech_started()
        t.submit_partial(tone(0.5))
        wait(lambda: bool(errors))
        assert not events
        t.submit_final(tone(1))
        wait_idle(t)
        assert fallback.calls == [16000]
        assert events[-1].text == "text1s" and events[-1].is_final
        assert usage == [("fake", "", 1.0)]
    finally:
        t.stop()


def test_both_providers_fail_still_retires_utterance():
    events = []
    t = RealtimeTranscriber(provider(Socket(fail=True), FakeSTT(fail=True)), events.append)
    t.start()
    try:
        t.speech_started()
        t.submit_final(tone(0.5))
        wait_idle(t)
        assert len(events) == 1 and events[0].is_final and events[0].text == ""
    finally:
        t.stop()


def test_both_failures_remain_visible_after_empty_final():
    updates = []
    t = RealtimeTranscriber(
        provider(Socket(fail=True), FakeSTT(fail=True)),
        lambda event: updates.append(("final", event.text)),
        lambda error: updates.append(("status", str(error))),
    )
    t.start()
    try:
        t.speech_started()
        t.submit_final(tone(0.5))
        wait_idle(t)
        assert updates[-2] == ("final", "")
        assert updates[-1][0] == "status" and "fallback transcription failed" in updates[-1][1]
    finally:
        t.stop()


def test_cancelled_fallback_failure_does_not_post_late_warning():
    entered, release = threading.Event(), threading.Event()

    class HeldFallback(FakeSTT):
        def transcribe(self, audio):
            entered.set()
            assert release.wait(3)
            raise RuntimeError("late failure")

    events, errors = [], []
    t = RealtimeTranscriber(
        provider(Socket(fail=True), HeldFallback()), events.append, errors.append
    )
    t.start()
    try:
        t.speech_started()
        t.submit_final(tone(0.5))
        assert entered.wait(2)
        t.cancel()
        errors.clear()
        release.set()
        wait_idle(t)
        assert not errors and not events
    finally:
        release.set()
        t.stop()


def test_cancellation_suppresses_late_text_and_allows_new_speech():
    entered, release = threading.Event(), threading.Event()

    class Slow:
        name = "fake"

        def stream(self, chunks, on_partial):
            entered.set()
            release.wait(2)
            on_partial("late")
            return "late"

        def transcribe(self, audio):
            raise AssertionError("cancelled speech must not fall back")

    events = []
    t = RealtimeTranscriber(Slow(), events.append)
    t.start()
    try:
        t.speech_started()
        assert entered.wait(1)
        t.cancel()
        release.set()
        wait_idle(t)
        assert not events
        t.speech_started()
        t.submit_final(tone(0.2))
        wait_idle(t)
        assert events[-1].utterance_id == 2
    finally:
        release.set()
        t.stop()


def test_sequential_questions_and_dual_sources_stay_separate():
    events = []
    transcribers = []
    for source in (DeviceKind.MIC, DeviceKind.LOOPBACK):
        p = SpeechmaticsProvider("fake", FakeSTT(), connector=lambda *a, **k: Socket())
        t = RealtimeTranscriber(p, events.append, source=source)
        transcribers.append(t)
        t.start()
    try:
        for t in transcribers:
            for _ in range(3):
                t.speech_started()
                t.submit_final(tone(0.3))
        for t in transcribers:
            wait_idle(t)
        finals = [e for e in events if e.is_final]
        assert len(finals) == 6
        for source in (DeviceKind.MIC, DeviceKind.LOOPBACK):
            assert [e.utterance_id for e in finals if e.source == source] == [1, 2, 3]
    finally:
        for t in transcribers:
            t.stop()


def test_factory_requires_key_and_chooses_available_fallback():
    with pytest.raises(ValueError, match="SPEECHMATICS_API_KEY"):
        build_provider(Settings(stt_provider="speechmatics", speechmatics_api_key=None))
    for key, expected in [(None, "local-whisper"), ("test", "openai")]:
        p = build_provider(
            Settings(
                stt_provider="speechmatics",
                speechmatics_api_key="test",
                openai_api_key=key,
            )
        )
        assert p.realtime
        assert p.fallback.name == expected


@pytest.mark.parametrize("mode", ["manual", "auto"])
def test_ui_capture_streams_and_stop_does_not_restart_from_queued_signals(qtbot, mode):
    from interview_assistant.app.controller import AssistantController
    from interview_assistant.audio.capture import AudioCapture
    from tests.test_app import FakeJev

    src = FakeSource()
    panel = AudioPanel(DEVS, capture_factory=lambda d, **kw: AudioCapture(src, 2, 48000, **kw))
    sockets = []

    def connect(*args, **kwargs):
        ws = Socket()
        sockets.append(ws)
        return ws

    p = SpeechmaticsProvider("test", FakeSTT(), connector=connect)
    jev = FakeJev()
    controller = AssistantController(jev, answer_mode=mode, poll_ms=10)
    window = MainWindow(panel, MeteredSTT(p, lambda *a: None), controller)
    qtbot.addWidget(window)
    window.show()
    assert panel.realtime and not sockets
    panel.start()
    src.push(np.concatenate([silence(0.3), tone(0.5)]))
    qtbot.waitUntil(lambda: bool(sockets) and len(sockets[0].audio) > 0)
    qtbot.waitUntil(lambda: "How many" in window.transcript_panel.view.toPlainText())
    assert not jev.asked  # live partials must not generate premature answers
    src.push(silence(0.6))
    qtbot.waitUntil(lambda: window.transcriber.idle())
    qtbot.waitUntil(lambda: "catalogs" in window.transcript_panel.view.toPlainText())
    if mode == "manual":
        qtbot.waitUntil(lambda: controller._pending_question is not None)
        assert not jev.asked
        controller.trigger_answer()
    qtbot.waitUntil(lambda: jev.asked == ["How many catalogs?"])
    panel.stop()
    panel.speech_changed.emit(True)
    qtbot.wait(30)
    assert len(sockets) == 1
    window.close()


def test_backlog_is_bounded_and_dropped_speech_is_retired(monkeypatch):
    from interview_assistant.stt import realtime

    monkeypatch.setattr(realtime, "MAX_BACKLOG", 2)
    started, release = threading.Event(), threading.Event()

    class Held:
        name = "fake"

        def stream(self, chunks, on_partial):
            started.set()
            release.wait(2)
            list(chunks)
            return "question"

        def transcribe(self, audio):
            return "fallback"

    events = []
    t = RealtimeTranscriber(Held(), events.append)
    t.start()
    try:
        t.speech_started()
        t.submit_final(tone(0.2))
        assert started.wait(1)
        for _ in range(5):
            t.speech_started()
            t.submit_final(tone(0.2))
        assert len(t._queue) == 2
        assert len([e for e in events if e.is_final and not e.text]) == 3
        release.set()
        wait_idle(t)
        assert sorted(e.utterance_id for e in events if e.is_final) == list(range(1, 7))
    finally:
        release.set()
        t.stop()


def test_midstream_disconnect_closes_sender_and_falls_back():
    class BrokenSocket(Socket):
        def send(self, data):
            if isinstance(data, bytes):
                raise ConnectionError("disconnected")
            super().send(data)

    events, errors = [], []
    ws = BrokenSocket()
    t = RealtimeTranscriber(provider(ws), events.append, errors.append)
    t.start()
    try:
        t.speech_started()
        t.submit_partial(tone(0.3))
        wait(lambda: bool(errors))
        t.submit_final(tone(1))
        wait_idle(t)
        assert events[-1].text == "text1s"
        assert ws.closed
        wait(lambda: not any(th.name == "speechmatics-send" for th in threading.enumerate()))
    finally:
        t.stop()


def test_cancel_during_stream_closes_socket_without_fallback():
    events = []
    ws, fallback = Socket(), FakeSTT()
    t = RealtimeTranscriber(provider(ws, fallback), events.append)
    t.start()
    try:
        t.speech_started()
        t.submit_partial(tone(0.3))
        wait(lambda: bool(events))
        t.cancel()
        wait_idle(t)
        assert ws.closed and not fallback.calls
        assert not any(e.is_final for e in events)
        wait(lambda: not any(th.name == "speechmatics-send" for th in threading.enumerate()))
    finally:
        t.stop()


def test_timeout_fallback_recovers_after_cooldown(monkeypatch):
    class SlowSocket(Socket):
        def recv(self, timeout):
            raise TimeoutError("network stalled")

    sockets, events = [], []

    def connect(*args, **kwargs):
        ws = SlowSocket() if not sockets else Socket()
        sockets.append(ws)
        return ws

    t = RealtimeTranscriber(
        SpeechmaticsProvider("test", FakeSTT(), connector=connect),
        events.append,
    )
    t.start()
    try:
        for _ in range(2):
            t.speech_started()
            t.submit_final(tone(1))
            wait_idle(t)
        assert len(sockets) == 1  # quota/network failures must not cause a reconnect storm
        assert [e.text for e in events if e.is_final] == ["text1s", "text1s"]
        t._retry_at = 0
        t.speech_started()
        t.submit_final(tone(1))
        wait_idle(t)
        assert len(sockets) == 2
        assert events[-1].text == "How many catalogs?"
    finally:
        t.stop()
