"""Full live pipeline: TTS -> loopback -> Whisper -> aggregator -> Jev -> overlay.

Usage: python scripts/e2e_live.py "<question to speak>" <out_dir>
Uses real audio devices and real API keys from .env; overlay settings go to a temp file."""

import subprocess
import sys
import tempfile
import time
import wave
import winsound
from pathlib import Path

from PySide6.QtCore import QSettings, QTimer
from PySide6.QtWidgets import QApplication

from interview_assistant.agents.jev import Jev, JevConfig
from interview_assistant.app.controller import AssistantController
from interview_assistant.audio.types import DeviceKind
from interview_assistant.config.settings import Settings
from interview_assistant.cost.factory import build_tracker
from interview_assistant.history.store import HistoryStore
from interview_assistant.llm.router_factory import build_router
from interview_assistant.stt.factory import build_provider
from interview_assistant.stt.metered import MeteredSTT
from interview_assistant.ui.audio_panel import AudioPanel
from interview_assistant.ui.main_window import MainWindow
from interview_assistant.ui.overlay import OverlayWindow

question = sys.argv[1]
out = Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)
wav = out / "question.wav"
ps = (
    "Add-Type -AssemblyName System.Speech; $s=New-Object System.Speech.Synthesis.SpeechSynthesizer;"
    f"$s.SetOutputToWaveFile('{wav}'); $s.Speak('{question}'); $s.Dispose()"
)
subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True)
with wave.open(str(wav)) as w:
    duration = w.getnframes() / w.getframerate()

settings = Settings(usage_log=False)
tracker = build_tracker(settings)
app = QApplication(sys.argv)
jev = Jev(
    build_router(settings, on_attempt=tracker.record_attempt),
    JevConfig(profile="Backend engineer, 5 years Python/Go, fintech."),
)
history = HistoryStore(Path(tempfile.mkdtemp()) / "history.db")
controller = AssistantController(jev, tracker=tracker, history=history)
tracker.on_change = controller.notify_cost_changed
overlay = OverlayWindow(
    controller, QSettings(str(Path(tempfile.mkdtemp()) / "o.ini"), QSettings.Format.IniFormat)
)
stt = build_provider(settings)
window = MainWindow(
    AudioPanel(),
    MeteredSTT(stt, tracker.record_stt),
    controller,
    overlay,
    partials=not getattr(stt, "metered", False),
    history=history,
)
window.show()
overlay.show()

t = {}
controller.answer_delta.connect(lambda g, d: t.setdefault("first_delta", time.monotonic()))
controller.question_started.connect(lambda g, q, s: t.setdefault("question", (time.monotonic(), q)))

panel = window.audio_panel
loop_idx = next(
    i
    for i in range(panel.device_combo.count())
    if panel.device_combo.itemData(i).kind is DeviceKind.LOOPBACK
)
panel.device_combo.setCurrentIndex(loop_idx)


def start_and_play() -> None:
    panel.start()
    t["play"] = time.monotonic()
    winsound.PlaySound(str(wav), winsound.SND_FILENAME | winsound.SND_ASYNC)


def check() -> None:
    done = overlay.answer_text and not overlay.stop_btn.isEnabled() and " · " in overlay.chip.text()
    if done and not t.get("followup") and overlay.question.text().startswith("🎤"):
        panel.stop()
        t["followup"] = True
        controller.ask("Why did you choose that approach?", "typed")
        return
    if t.get("followup") and not overlay.question.text().startswith("⌨"):
        done = False
    if done or time.monotonic() - t["play"] > 90:
        panel.stop()
        QTimer.singleShot(400, finish)
        timer.stop()


def finish() -> None:
    speech_end = t["play"] + duration
    print("TRANSCRIPT :", " | ".join(window.transcript_panel.final_texts))
    print("QUESTION   :", t.get("question", (0, "-"))[1])
    print("CHIP       :", overlay.chip.text())
    if "first_delta" in t:
        print(f"first answer token {t['first_delta'] - speech_end:.1f}s after speech ended")
    print("ANSWER     :", overlay.answer_text[:400].replace("\n", " / "))
    sm = tracker.summary()
    print(
        f"COST       : total ${sm.total_usd:.6f} (llm ${sm.llm_usd:.6f}, stt ${sm.stt_usd:.6f}) "
        f"tokens in/out {sm.input_tokens}/{sm.output_tokens} "
        f"stt_audio {sm.audio_s:.1f}s (wav {duration:.1f}s)"
    )
    print(
        "BY MODEL   :",
        {k: round(v, 6) for k, v in sm.by_model.items()},
        "by tag:",
        {k: round(v, 6) for k, v in sm.by_tag.items()},
    )
    print("OVERLAY    :", overlay.cost_label.text(), "| chip:", overlay.chip.text())
    print("HISTORY    :", history.counts(), [(x.title, x.turns) for x in history.list_sessions()])
    for tt in history.get_turns(history.list_sessions()[0].id):
        print(
            f"  saved: [{tt.source}/{tt.status}/{tt.category}/{tt.decision_source}] {tt.question!r}"
            f" -> {len(tt.answer)} chars via {tt.route} cost={tt.cost_usd}"
        )
    window.show_history()  # what the History button does
    hw = window.history_window
    QApplication.processEvents()
    hw.grab().save(str(out / "history_browse.png"))
    hw.search.setText("thread")
    hw._run_search()
    QApplication.processEvents()
    print("SEARCH     :", hw.status.text(), "| sessions listed:", hw.sessions.count())
    hw.grab().save(str(out / "history.png"))
    overlay.grab().save(str(out / "overlay.png"))
    window.grab().save(str(out / "main.png"))
    controller.shutdown()
    app.quit()


timer = QTimer()
timer.timeout.connect(check)
QTimer.singleShot(4000, lambda: (start_and_play(), timer.start(250)))  # let Whisper warm up
sys.exit(app.exec())
