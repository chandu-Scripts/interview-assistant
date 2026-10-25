import logging
import sqlite3
import sys
import threading
import time
import tracemalloc
from unittest.mock import MagicMock

import numpy as np
import pytest

from interview_assistant.app.controller import AssistantController
from interview_assistant.app.single_instance import SingleInstance
from interview_assistant.audio.capture import AudioCapture, WasapiSource
from interview_assistant.audio.devices import default_device, list_devices
from interview_assistant.audio.types import DeviceKind
from interview_assistant.audio.vad import SpeechSegmenter
from interview_assistant.history.store import HistoryStore, TurnRecord
from interview_assistant.logging_setup import (
    LOG_NAME,
    RedactingFilter,
    install_crash_handlers,
    setup_logging,
)
from interview_assistant.stt import streaming as streaming_mod
from interview_assistant.stt.base import TranscriptEvent
from interview_assistant.stt.local_whisper import FasterWhisperProvider
from interview_assistant.stt.streaming import StreamingTranscriber
from interview_assistant.ui.audio_panel import AudioPanel
from interview_assistant.ui.overlay import OverlayWindow
from interview_assistant.ui.transcript_panel import MAX_UTTERANCES, TranscriptPanel
from tests.test_app import FakeJev
from tests.test_audio import DEVS, FakeSource, silence, tone

# ---------------- logging ----------------


def record(msg, *args, exc_info=None):
    return logging.LogRecord("t", logging.INFO, __file__, 1, msg, args, exc_info)


@pytest.mark.parametrize(
    ("raw", "gone"),
    [
        ("key sk-abcdefghijklmnopqrstuvwxyz123456 failed", "sk-abcdefghijklmnopqrstuvwxyz123456"),
        ("mail bob@example.com now", "bob@example.com"),
        ("call 415-555-0132", "415-555-0132"),
        ("Bearer abcdefghijklmnopqrstuvwxyz12", "abcdefghijklmnopqrstuvwxyz12"),
    ],
)
def test_filter_masks_secrets_in_messages_and_args(raw, gone):
    r = record("%s", raw)
    assert RedactingFilter().filter(r) and gone not in r.getMessage()


def test_filter_masks_exception_text_and_survives_bad_format():
    try:
        raise ValueError("boom for bob@example.com with sk-abcdefghijklmnopqrstuvwxyz123456")
    except ValueError:
        r = record("failed", exc_info=sys.exc_info())
    assert RedactingFilter().filter(r)
    assert "bob@example.com" not in r.exc_text and "sk-abcdefghij" not in r.exc_text
    bad = record("%d items", "not-a-number")
    assert RedactingFilter().filter(bad)  # a malformed format string must not lose the line


def test_setup_logging_writes_a_redacted_file(tmp_path):
    path = setup_logging("INFO", tmp_path)
    assert path == tmp_path / "logs" / LOG_NAME
    log = logging.getLogger("interview_assistant.test")
    log.info("user bob@example.com used sk-abcdefghijklmnopqrstuvwxyz123456")
    for h in logging.getLogger().handlers:
        h.flush()
    text = path.read_text(encoding="utf-8")
    assert "user [EMAIL] used [SECRET]" in text and "bob@example.com" not in text


def test_setup_logging_survives_unwritable_data_dir(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    assert setup_logging("INFO", blocker) is None  # a file where a folder should be


def test_no_question_text_is_ever_logged(qtbot, tmp_path):
    marker = "zzqxmarkerword"
    path = setup_logging("DEBUG", tmp_path)
    c = AssistantController(FakeJev(), poll_ms=10)
    try:
        c.ask(f"What is a mutex? {marker}", "voice")
        qtbot.wait(400)
    finally:
        c.shutdown()
    for h in logging.getLogger().handlers:
        h.flush()
    assert marker not in path.read_text(encoding="utf-8")


def test_crash_handlers_log_and_notify_once(tmp_path):
    path = setup_logging("INFO", tmp_path)
    seen = []
    old_hook, old_thread_hook = sys.excepthook, threading.excepthook
    try:
        install_crash_handlers(seen.append)
        try:
            raise RuntimeError("kaboom bob@example.com")
        except RuntimeError:
            sys.excepthook(*sys.exc_info())
            sys.excepthook(*sys.exc_info())  # identical error: the user is told only once
        t = threading.Thread(target=lambda: 1 / 0)
        t.start()
        t.join()
        for h in logging.getLogger().handlers:
            h.flush()
        text = path.read_text(encoding="utf-8")
        assert seen[0].startswith("RuntimeError") and len(seen) == 2  # + the ZeroDivisionError
        assert "Unhandled exception" in text and "bob@example.com" not in text
        assert "ZeroDivisionError" in text
    finally:
        sys.excepthook, threading.excepthook = old_hook, old_thread_hook


def test_keyboard_interrupt_still_uses_default_hook(monkeypatch):
    old = sys.excepthook
    called = []
    monkeypatch.setattr(sys, "__excepthook__", lambda *a: called.append(a))
    try:
        install_crash_handlers()
        sys.excepthook(KeyboardInterrupt, KeyboardInterrupt(), None)
        assert called
    finally:
        sys.excepthook = old


# ---------------- single instance ----------------
def test_only_one_instance_and_release_allows_relaunch(qtbot, tmp_path):
    a, b = SingleInstance(tmp_path, "ia-test-a"), SingleInstance(tmp_path, "ia-test-a")
    assert a.acquire() is True
    assert b.acquire() is False  # a second copy is refused while the first runs
    a.release()
    assert b.acquire() is True  # e.g. the restart after a settings change
    b.release()


def test_second_launch_raises_the_first(qtbot, tmp_path):
    first, second = SingleInstance(tmp_path, "ia-test-b"), SingleInstance(tmp_path, "ia-test-b")
    raised = []
    first.on_raise = lambda: raised.append(1)
    assert first.acquire()
    assert not second.acquire()
    proc = second_launch(tmp_path, "ia-test-b")
    qtbot.waitUntil(lambda: raised == [1], timeout=15000)
    assert proc.wait(15) == 0  # it was refused AND could reach the running copy
    first.release()
    assert second.notify_existing(timeout_ms=200) is False  # nobody is listening any more


def second_launch(lock_dir, name):
    """A genuinely separate process behaving like a second app launch."""
    import os
    import subprocess

    code = (
        "import sys\n"
        "from pathlib import Path\n"
        "from PySide6.QtWidgets import QApplication\n"
        "from interview_assistant.app.single_instance import SingleInstance\n"
        "app = QApplication([])\n"
        f"s = SingleInstance(Path({str(lock_dir)!r}), {name!r})\n"
        "sys.exit(0 if (not s.acquire() and s.notify_existing()) else 1)\n"
    )
    return subprocess.Popen(
        [sys.executable, "-c", code], env={**os.environ, "QT_QPA_PLATFORM": "offscreen"}
    )


def test_raise_callback_errors_do_not_break_the_server(qtbot, tmp_path):
    first = SingleInstance(tmp_path, "ia-test-c")
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("ui not ready")

    first.on_raise = flaky
    first.acquire()
    for expected in (1, 2):  # the first callback raises; the server must still serve the next
        proc = second_launch(tmp_path, "ia-test-c")
        qtbot.waitUntil(lambda n=expected: len(calls) == n, timeout=15000)
        assert proc.wait(15) == 0
    first.release()


# ---------------- audio watchdog ----------------
class MortalSource(FakeSource):
    def __init__(self):
        super().__init__()
        self.alive_flag = True

    def alive(self):
        return self.alive_flag


def test_capture_health_reports_dead_device_only_while_recording():
    src = MortalSource()
    cap = AudioCapture(src, 1, 16000)
    assert cap.health() is None  # not recording
    cap.start()
    assert cap.health() is None
    src.alive_flag = False
    assert "stopped" in cap.health()
    cap.stop()
    assert cap.health() is None


def test_sources_without_liveness_are_never_flagged():
    cap = AudioCapture(FakeSource(), 1, 16000)
    cap.start()
    assert cap.health() is None
    cap.stop()


def test_panel_watchdog_stops_capture_and_reports(qtbot):
    src = MortalSource()
    panel = AudioPanel(DEVS, lambda d, **kw: AudioCapture(src, d.channels, d.sample_rate, **kw))
    qtbot.addWidget(panel)
    panel.toggle_button.click()
    assert panel.recording
    panel.check_health()
    assert panel.recording  # healthy: no false alarm
    src.alive_flag = False
    with qtbot.waitSignal(panel.capture_failed) as sig:
        panel.check_health()
    assert "stopped" in sig.args[0] and not panel.recording
    assert "Start capture" in panel.error_label.text()
    src.alive_flag = True
    panel.toggle_button.click()  # the user can simply try again
    assert panel.recording
    panel.stop()


def test_panel_watchdog_timer_runs_by_itself(qtbot):
    src = MortalSource()
    panel = AudioPanel(DEVS, lambda d, **kw: AudioCapture(src, d.channels, d.sample_rate, **kw))
    qtbot.addWidget(panel)
    panel.toggle_button.click()
    src.alive_flag = False
    qtbot.waitUntil(lambda: not panel.recording, timeout=4000)
    assert "stopped" in panel.error_label.text()


def test_wasapi_source_stop_survives_a_dead_device():
    src = WasapiSource(DEVS[0])
    src._stream, src._pa = MagicMock(), MagicMock()
    src._stream.stop_stream.side_effect = OSError("device gone")
    src._stream.close.side_effect = OSError("device gone")
    src._pa.terminate.side_effect = OSError("device gone")
    src.stop()  # must not raise, and must drop the handles
    assert src._stream is None and src._pa is None
    assert src.alive() is False


@pytest.mark.skipif(sys.platform != "win32", reason="WASAPI")
def test_real_loopback_is_not_flagged_during_silence_but_is_when_stream_dies():
    dev = default_device(DeviceKind.LOOPBACK, list_devices())
    if dev is None:
        pytest.skip("no loopback device")
    cap = AudioCapture.for_device(dev)
    cap.start()
    try:
        for _ in range(6):  # 3 s: a quiet loopback delivers no data, which is normal
            time.sleep(0.5)
            assert cap.health() is None
        cap._source._stream.stop_stream()  # what a vanished device looks like to PortAudio
        assert cap.health() is not None
    finally:
        cap.stop()


# ---------------- database recovery ----------------
def test_garbage_database_is_set_aside_and_replaced(tmp_path):
    path = tmp_path / "history.db"
    path.write_bytes(b"this is definitely not a sqlite database" * 100)
    store = HistoryStore(path)
    assert store.recovered_from is not None and store.recovered_from.exists()
    assert store.recovered_from.read_bytes().startswith(b"this is definitely")  # nothing destroyed
    assert store.add_turn(TurnRecord("works again")) and store.counts() == (1, 1)
    store.close()
    assert HistoryStore(path).recovered_from is None  # the repaired file opens cleanly next time


def test_truncated_database_is_recovered(tmp_path):
    path = tmp_path / "history.db"
    s = HistoryStore(path)
    for i in range(200):
        s.add_turn(TurnRecord(f"question {i}", "answer " * 50))
    s.close()
    data = path.read_bytes()
    path.write_bytes(data[: len(data) // 2])
    store = HistoryStore(path)
    assert store.recovered_from is not None
    assert store.add_turn(TurnRecord("fresh")) and store.counts()[1] >= 1
    store.close()


def test_healthy_and_newer_databases_are_left_alone(tmp_path):
    path = tmp_path / "history.db"
    s = HistoryStore(path)
    s.add_turn(TurnRecord("keep"))
    s.close()
    s2 = HistoryStore(path)
    assert s2.recovered_from is None and s2.counts() == (1, 1)
    s2.close()
    c = sqlite3.connect(path)
    c.execute("PRAGMA user_version = 99")
    c.commit()
    c.close()
    with pytest.raises(RuntimeError):
        HistoryStore(path)  # a newer app's data is refused, never quarantined or wiped
    assert not list(tmp_path.glob("*.corrupt-*"))


# ---------------- bounded memory / backpressure ----------------
def test_transcript_panel_is_bounded(qtbot):
    p = TranscriptPanel()
    qtbot.addWidget(p)
    p._render = lambda: None  # rendering is not what is measured here
    for i in range(1, MAX_UTTERANCES * 4 + 1):
        p.apply(TranscriptEvent(i, f"partial {i}", False, 1))
        p.apply(TranscriptEvent(i, f"final {i}", True, 1))
    assert len(p._finals) == MAX_UTTERANCES and len(p._partials) == 0
    assert p.final_texts[-1] == f"final {MAX_UTTERANCES * 4}"  # newest kept
    for i in range(10_000, 10_000 + MAX_UTTERANCES * 2):  # partials that never finalize
        p.apply(TranscriptEvent(i, "x", False, 1))
    assert len(p._partials) <= MAX_UTTERANCES


class SlowSTT:
    name = "slow"

    def __init__(self, delay):
        self.delay, self.n = delay, 0

    def warmup(self):
        pass

    def transcribe(self, pcm):
        self.n += 1
        time.sleep(self.delay)
        return "t"


def test_stt_backlog_warns_once_and_is_capped(monkeypatch):
    monkeypatch.setattr(streaming_mod, "MAX_BACKLOG", 6)
    errors, events = [], []
    stt = SlowSTT(0.15)
    tr = StreamingTranscriber(stt, events.append, errors.append)
    tr.start()
    for _ in range(12):  # speech arrives much faster than it can be transcribed
        tr.speech_started()
        tr.submit_final(np.zeros(1600, dtype=np.int16))
    while not tr.idle():
        time.sleep(0.05)
    tr.stop()
    finals = [e for e in events if e.is_final]
    assert len(finals) == 12  # dropped speech must retire its pending aggregation slot
    assert stt.n == sum(bool(event.text) for event in finals) < 12
    assert finals[-1].utterance_id == 12
    assert len(errors) == 1 and "falling behind" in str(errors[0])


def test_no_warning_when_recognition_keeps_up():
    errors = []
    tr = StreamingTranscriber(SlowSTT(0.0), lambda e: None, errors.append)
    tr.start()
    for _ in range(50):
        tr.speech_started()
        tr.submit_final(np.zeros(1600, dtype=np.int16))
        while not tr.idle():
            time.sleep(0.001)
    tr.stop()
    assert errors == []


def test_finalized_set_is_pruned():
    tr = StreamingTranscriber(SlowSTT(0.0), lambda e: None)
    tr.start()
    for _ in range(1300):
        tr.speech_started()
        tr.submit_final(np.zeros(160, dtype=np.int16))
        while not tr.idle():
            time.sleep(0.0005)
    tr.stop()
    assert len(tr._finalized) <= 700


def test_soak_vad_memory_is_flat_over_hours_of_audio():
    """~30 minutes of conversation-like audio: memory must not keep growing."""
    count = [0]
    # Count segments but do not keep them: retaining audio would be the test's own "leak".
    vad = SpeechSegmenter(lambda _s: count.__setitem__(0, count[0] + 1))
    unit = np.concatenate([tone(4.0), silence(6.0)])  # a 4 s utterance every 10 s

    def feed(minutes):
        for _ in range(minutes * 6):
            for i in range(0, len(unit), 1600):
                vad.feed(unit[i : i + 1600])

    tracemalloc.start()
    feed(5)  # warm-up
    count[0] = 0
    base = tracemalloc.take_snapshot()
    feed(25)
    grown = sum(s.size_diff for s in tracemalloc.take_snapshot().compare_to(base, "filename"))
    tracemalloc.stop()
    assert count[0] == 150  # every utterance detected, none merged or lost
    assert grown < 500_000, f"grew by {grown / 1e6:.2f} MB over 25 minutes of audio"


# ---------------- latency breakdown ----------------
def test_voice_question_reports_stage_timings(qtbot, tmp_path):
    from PySide6.QtCore import QSettings

    from interview_assistant.app.aggregator import UtteranceAggregator

    c = AssistantController(
        FakeJev(), aggregator=UtteranceAggregator(quiet_s=0.05), poll_ms=10, answer_mode="auto"
    )
    o = OverlayWindow(c, QSettings(str(tmp_path / "o.ini"), QSettings.Format.IniFormat))
    qtbot.addWidget(o)
    got = []
    c.latency_ready.connect(lambda g, t: got.append(t))
    try:
        c.on_speech_started()
        c.on_speech_ended()
        c.on_transcript_event(TranscriptEvent(1, "What is a mutex?", True, 1))
        qtbot.waitUntil(lambda: len(got) == 1, timeout=3000)
    finally:
        c.shutdown()
    t = got[0]
    assert 0 <= t["speech_to_question_s"] < 2 and t["question_to_first_token_s"] is not None
    assert t["total_s"] >= t["question_to_first_token_s"]
    tip = o.chip.toolTip()
    assert "transcribe" in tip and "first answer word" in tip


def test_typed_question_has_no_speech_stage(qtbot):
    c = AssistantController(FakeJev(), poll_ms=10)
    got = []
    c.latency_ready.connect(lambda g, t: got.append(t))
    try:
        c.ask("What is a mutex?")
        qtbot.waitUntil(lambda: len(got) == 1, timeout=3000)
    finally:
        c.shutdown()
    assert got[0]["speech_to_question_s"] is None


# ---------------- speech vocabulary hint ----------------
def test_provider_passes_the_vocabulary_hint(monkeypatch):
    p = FasterWhisperProvider("base.en", initial_prompt="Terms: Kubernetes, OAuth")
    model = MagicMock()
    model.transcribe.return_value = ([], None)
    p._model = model
    p.transcribe(np.zeros(16000, dtype=np.int16))
    assert model.transcribe.call_args.kwargs["initial_prompt"] == "Terms: Kubernetes, OAuth"
    assert FasterWhisperProvider("base.en", initial_prompt="")._initial_prompt is None
