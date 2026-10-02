import ctypes
import pathlib
import re
import sys

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QWidget

from interview_assistant.agents.jev import Jev, JevConfig
from interview_assistant.app.controller import AssistantController
from interview_assistant.app.hotkeys import MSG, WM_HOTKEY, GlobalHotkeys, parse_hotkey
from interview_assistant.app.presentation import PresentationMode
from interview_assistant.cost.pricing import PriceBook
from interview_assistant.cost.tracker import CostTracker, UsageStore
from interview_assistant.history.store import HistoryStore, TurnRecord
from interview_assistant.llm.router import Route, Router, RouterConfig
from interview_assistant.llm.types import LLMRequest, StreamEvent, Usage
from interview_assistant.privacy.consent import ConsentGate
from interview_assistant.privacy.disclosure import CONSENT_TEXT, describe_data_flow
from interview_assistant.privacy.redact import redact, redact_text
from interview_assistant.privacy.state import PrivacyState
from interview_assistant.ui.audio_panel import AudioPanel
from interview_assistant.ui.main_window import MainWindow
from interview_assistant.ui.privacy_dialog import PrivacyDialog
from interview_assistant.ui.tray import TrayController
from tests.test_app import FakeJev
from tests.test_audio import DEVS, FakeSource

# ---------------- redaction ----------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("mail me at jane.doe+work@example.co.uk please", "mail me at [EMAIL] please"),
        ("call +1 (415) 555-0132 or 415-555-0132", "call [PHONE] or [PHONE]"),
        ("phone: +44 20 7946 0958", "phone: [PHONE]"),
        ("SSN 123-45-6789 on file", "SSN [SSN] on file"),
        ("card 4111 1111 1111 1111 expires", "card [CARD] expires"),
        ("card 4111-1111-1111-1111", "card [CARD]"),
        ("key sk-abcdefghijklmnopqrstuvwxyz123456", "key [SECRET]"),
        ("key sk-ant-api03-abcdefghijklmnopqrstuvwxyz", "key [SECRET]"),
        ("AIzaSyA1234567890abcdefghijklmnopqrstuv", "[SECRET]"),
        ("token ghp_abcdefghijklmnopqrstuvwxyz0123456789", "token [SECRET]"),
        ("aws AKIAIOSFODNN7EXAMPLE", "aws [SECRET]"),
        ("Authorization: Bearer abcdefghijklmnopqrstuvwxyz12", "Authorization: [SECRET]"),
    ],
)
def test_redacts_sensitive_values(text, expected):
    assert redact_text(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "We served 10,000,000 users in 2026",
        "version 3.14.2 shipped on 2026-09-27",
        "handled 1234567890 requests",  # 10 digits but no phone-like separators
        "card number 4111 1111 1111 1112 is not Luhn valid",
        "Tell me about a time you resolved a conflict.",
        "ünïcödé text and 日本語 stay untouched",
        "",
    ],
)
def test_leaves_ordinary_text_alone(text):
    assert redact_text(text) == text.replace("4111 1111 1111 1112", "4111 1111 1111 1112")


def test_redaction_counts_and_idempotence():
    r = redact("a@b.com, c@d.org and 415-555-0132")
    assert r.changed and r.counts["EMAIL"] == 2 and r.counts["PHONE"] == 1
    assert redact(r.text).text == r.text and not redact(r.text).changed


# ---------------- Jev redaction (what actually leaves the PC) ----------------
class Spy:
    name = "a"
    default_model = "m"

    def __init__(self):
        self.requests: list[LLMRequest] = []

    async def stream(self, request):
        self.requests.append(request)
        yield StreamEvent(delta="ok", usage=Usage(1, 1))


def make_jev(redact_on):
    spy = Spy()
    chain = [Route("a", "m")]
    router = Router({"a": spy}, RouterConfig(chains={"fast": chain, "smart": chain}))
    cfg = JevConfig(profile="Jane, jane@corp.com, 415-555-0132", redact=redact_on)
    return Jev(router, cfg), spy


async def drain(jev, q):
    return [e async for e in jev.ask(q)]


async def test_jev_masks_profile_question_and_history_when_on():
    jev, spy = make_jev(True)
    await drain(jev, "Explain how OAuth works? My email is bob@x.io")
    await drain(jev, "What is a mutex, call me at 415-555-0199?")
    for req in spy.requests:
        blob = (req.system or "") + " ".join(m.content for m in req.messages)
        assert "@" not in blob and "555" not in blob
    assert "[EMAIL]" in spy.requests[0].messages[-1].content
    # the local history/memory keeps the original text; only the outbound copy is masked
    assert "bob@x.io" in jev.history[0].question


async def test_jev_sends_text_unchanged_when_off_and_toggles_live():
    jev, spy = make_jev(False)
    await drain(jev, "Explain how OAuth works? My email is bob@x.io")
    assert "bob@x.io" in spy.requests[0].messages[-1].content
    assert "jane@corp.com" in spy.requests[0].system
    jev.config.redact = True  # switched on mid-session (e.g. from the privacy dialog)
    await drain(jev, "Explain how caching works? mail bob@x.io")
    assert "bob@x.io" not in spy.requests[1].messages[-1].content
    assert all("bob@x.io" not in m.content for m in spy.requests[1].messages)  # history too


# ---------------- disclosure ----------------
def test_data_flow_local_vs_cloud():
    local = describe_data_flow(
        "local-whisper", ["openai", "gemini"], True, True, True, False, "C:/d"
    )
    text = "\n".join(f.line() for f in local)
    assert "offline" in text and "gemini, openai" in text and "masked" in text
    assert local[0].leaves_pc is False and local[1].leaves_pc is True
    cloud = describe_data_flow("openai", [], False, False, False, False, "C:/d")
    assert cloud[0].leaves_pc and "no AI provider" in cloud[1].where and "OFF" in cloud[2].where
    inc = describe_data_flow("local-whisper", ["openai"], False, True, True, True, "C:/d")
    assert "incognito is ON" in inc[2].where
    saved = describe_data_flow("local-whisper", ["openai"], False, True, True, False, "C:/data")
    assert saved[2].where == "C:/data" and not saved[2].leaves_pc


# ---------------- consent ----------------
def test_consent_gate(tmp_path):
    s = QSettings(str(tmp_path / "c.ini"), QSettings.Format.IniFormat)
    prompts = []

    def prompt(text):
        prompts.append(text)
        return answer[0]

    answer = [False]
    gate = ConsentGate(s, prompt)
    assert not gate.granted and gate.ensure() is False and prompts == [CONSENT_TEXT]
    assert gate.ensure() is False and len(prompts) == 2  # declining is not remembered
    answer[0] = True
    assert gate.ensure() is True
    assert gate.ensure() is True and len(prompts) == 3  # remembered: no more prompts
    assert ConsentGate(s, prompt).granted  # persisted across instances
    gate.revoke()
    assert not gate.granted
    assert ConsentGate(s, prompt, required=False).ensure() is True and len(prompts) == 3


def test_audio_panel_blocks_capture_without_consent(qtbot):
    src = FakeSource()
    from interview_assistant.audio.capture import AudioCapture

    panel = AudioPanel(DEVS, lambda d, **kw: AudioCapture(src, d.channels, d.sample_rate, **kw))
    qtbot.addWidget(panel)
    panel.before_start = lambda: False
    panel.toggle_button.click()
    assert not panel.recording and src.started == 0 and "not started" in panel.error_label.text()
    panel.before_start = lambda: True
    panel.toggle_button.click()
    assert panel.recording and src.started == 1
    panel.stop()


# ---------------- store / tracker privacy switches ----------------
def test_history_suspend_is_separate_from_user_preference():
    h = HistoryStore(":memory:")
    h.suspended = True
    assert h.add_turn(TurnRecord("q")) is None and h.enabled and not h.writable
    h.suspended = False
    assert h.add_turn(TurnRecord("q"))
    h.enabled = False
    assert h.add_turn(TurnRecord("q2")) is None
    h.close()


def test_tracker_persist_flag_and_wipe(tmp_path):
    path = tmp_path / "u.jsonl"
    t = CostTracker(PriceBook(), UsageStore(path))
    t.record_stt("openai", "whisper-1", 60)
    assert path.exists()
    t.persist = False
    t.record_stt("openai", "whisper-1", 60)
    assert len(path.read_text(encoding="utf-8").splitlines()) == 1  # 2nd call stayed in memory
    assert t.summary("all").calls == 2
    t.wipe()
    assert not path.exists() and t.summary("all").calls == 0


def test_privacy_state_incognito_redact_and_wipe(tmp_path, qtbot):
    h = HistoryStore(tmp_path / "h.db")
    t = CostTracker(PriceBook(), UsageStore(tmp_path / "u.jsonl"))
    cfg = JevConfig()
    st = PrivacyState(h, t, cfg, incognito=False, redact=True)
    assert cfg.redact and not st.incognito
    h.add_turn(TurnRecord("before incognito"))
    sid = h.current_session
    with qtbot.waitSignal(st.changed):
        st.set_incognito(True)
    assert h.suspended and not t.persist and h.current_session is None
    assert h.add_turn(TurnRecord("secret")) is None
    t.record_stt("openai", "whisper-1", 30)
    assert not (tmp_path / "u.jsonl").exists()
    st.set_incognito(False)
    h.add_turn(TurnRecord("after"))
    assert h.current_session != sid and h.counts() == (2, 2)  # a fresh session
    st.set_redact(False)
    assert not cfg.redact
    t.record_stt("openai", "whisper-1", 30)
    with qtbot.waitSignal(st.changed):
        st.wipe_all()
    assert h.counts() == (0, 0) and not (tmp_path / "u.jsonl").exists()


def test_privacy_state_starts_incognito_from_settings():
    h = HistoryStore(":memory:")
    st = PrivacyState(h, None, JevConfig(), incognito=True)
    assert st.incognito and h.suspended
    assert h.add_turn(TurnRecord("nope")) is None


# ---------------- presentation mode ----------------
class FakeAudio:
    def __init__(self):
        self.stops = 0

    def stop(self):
        self.stops += 1


class FakeCtl:
    def __init__(self):
        self.stops, self.paused = 0, []

    def stop(self):
        self.stops += 1

    def set_paused(self, p):
        self.paused.append(p)


@pytest.fixture
def widgets(qtbot):
    ws = [QWidget() for _ in range(4)]
    for w in ws:
        qtbot.addWidget(w)
    return ws


def test_presentation_off_by_default_and_activate_deactivate(qtbot, widgets):
    overlay, hist, dialog, main = widgets
    for w in (overlay, main):
        w.show()  # history and dialog are closed: they must stay closed afterwards
    audio, ctl = FakeAudio(), FakeCtl()
    pm = PresentationMode([overlay, hist, dialog], main, audio, ctl)
    assert not pm.active
    with qtbot.waitSignal(pm.changed) as sig:
        pm.activate()
    assert sig.args == [True] and pm.active
    assert not overlay.isVisible() and main.isMinimized()
    assert audio.stops == 1 and ctl.stops == 1 and ctl.paused == [True]
    pm.activate()  # idempotent
    assert audio.stops == 1
    with qtbot.waitSignal(pm.changed) as sig:
        pm.deactivate()
    assert sig.args == [False] and not pm.active
    assert overlay.isVisible() and not hist.isVisible() and not dialog.isVisible()
    assert main.isVisible() and not main.isMinimized()
    assert ctl.paused == [True, False]
    assert audio.stops == 1  # capture is never restarted automatically


def test_presentation_toggle_and_no_windows(qtbot):
    pm = PresentationMode([], None)
    pm.toggle()
    assert pm.active
    pm.toggle()
    assert not pm.active


def test_presentation_respects_windows_the_user_had_hidden(qtbot, widgets):
    overlay, hist, _, main = widgets
    hist.show()  # user had only the history window open
    pm = PresentationMode([overlay, hist], main)
    pm.activate()
    assert not hist.isVisible()
    pm.deactivate()
    assert hist.isVisible() and not overlay.isVisible()


def test_paused_controller_ignores_questions(qtbot):
    jev = FakeJev()
    c = AssistantController(jev, poll_ms=10)
    try:
        c.set_paused(True)
        assert c.paused
        c.ask("What is a mutex?")
        c.ask("What is a mutex?", "voice")
        c.on_speech_started()
        qtbot.wait(150)
        assert jev.asked == []
        c.set_paused(False)
        c.ask("What is a semaphore?")
        qtbot.waitUntil(lambda: jev.asked == ["What is a semaphore?"], timeout=3000)
    finally:
        c.shutdown()


def test_source_never_hides_windows_from_capture():
    """Presentation mode hides windows on this PC only - no capture-evasion APIs anywhere."""
    src = pathlib.Path(__file__).parents[1] / "src" / "interview_assistant"
    banned = [
        "SetWindowDisplayAffinity",
        "WDA_EXCLUDEFROMCAPTURE",
        "WDA_MONITOR",
        "ExcludeFromCapture",
    ]
    for py in src.rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        for word in banned:
            assert word not in text, (py, word)


# ---------------- hotkeys ----------------
def test_parse_hotkey():
    assert parse_hotkey("ctrl+alt+p") == (0x2 | 0x1, ord("P"))
    assert parse_hotkey("Ctrl + Shift + F12") == (0x2 | 0x4, 0x7B)
    assert parse_hotkey("win+1") == (0x8, ord("1"))
    for bad in ("p", "ctrl+", "ctrl+alt+enter", "hyper+p", "ctrl+f13", ""):
        with pytest.raises(ValueError):
            parse_hotkey(bad)


def fake_msg(message, wparam):
    m = MSG()
    m.message, m.wParam = message, wparam
    return m


def test_native_filter_dispatches_only_registered_hotkeys():
    hk = GlobalHotkeys()
    calls = []
    hk._callbacks[7] = lambda: calls.append("seven")
    hk._callbacks[8] = lambda: 1 / 0
    ev = b"windows_generic_MSG"
    m = fake_msg(WM_HOTKEY, 7)
    assert hk.nativeEventFilter(ev, ctypes.addressof(m)) == (True, 0) and calls == ["seven"]
    m2 = fake_msg(WM_HOTKEY, 99)  # unknown id: not ours, let it through
    assert hk.nativeEventFilter(ev, ctypes.addressof(m2)) == (False, 0)
    m3 = fake_msg(0x0100, 7)  # not WM_HOTKEY
    assert hk.nativeEventFilter(ev, ctypes.addressof(m3)) == (False, 0)
    assert hk.nativeEventFilter(b"other", ctypes.addressof(m)) == (False, 0)
    m4 = fake_msg(WM_HOTKEY, 8)  # a failing callback must not propagate
    assert hk.nativeEventFilter(ev, ctypes.addressof(m4)) == (True, 0)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows hotkeys")
def test_real_registration_roundtrip_and_conflict():
    a, b = GlobalHotkeys(), GlobalHotkeys()
    combo = "ctrl+alt+shift+f11"
    assert a.register(combo, lambda: None) is True
    assert b.register(combo, lambda: None) is False  # the OS refuses a duplicate
    assert a.register("", lambda: None) is False
    a.unregister_all()
    assert b.register(combo, lambda: None) is True  # free again
    b.unregister_all()


# ---------------- tray ----------------
def test_tray_actions_and_state(qtbot):
    calls = []
    t = TrayController(
        *(lambda n=n: calls.append(n) for n in ("overlay", "pres", "inc", "main", "quit"))
    )
    t.overlay_action.trigger()
    t.presentation_action.trigger()
    t.incognito_action.trigger()
    assert calls == ["overlay", "pres", "inc"]
    t.set_state(True, False)
    assert t.presentation_action.isChecked() and "PRESENTATION MODE" in t.tray.toolTip()
    t.set_state(False, True)
    assert not t.presentation_action.isChecked() and "INCOGNITO" in t.tray.toolTip()
    t.set_state(False, False)
    assert t.tray.toolTip() == "Interview Assistant"
    if not TrayController.available():
        assert t.show() is False


# ---------------- privacy dialog + banner ----------------
@pytest.fixture
def privacy_stack(qtbot, tmp_path):
    h = HistoryStore(tmp_path / "h.db")
    t = CostTracker(PriceBook(), UsageStore(tmp_path / "u.jsonl"))
    st = PrivacyState(h, t, JevConfig(), redact=True)
    w1, w2 = QWidget(), QWidget()
    for w in (w1, w2):
        qtbot.addWidget(w)
    pm = PresentationMode([w1], w2, FakeAudio(), FakeCtl())
    asked = []

    def confirm(text):
        asked.append(text)
        return answer[0]

    answer = [False]
    dlg = PrivacyDialog(
        st,
        pm,
        lambda: describe_data_flow(
            "local-whisper", ["openai"], False, st.redact, True, st.incognito, "C:/data"
        ),
        "ctrl+alt+p = presentation",
        confirm,
    )
    qtbot.addWidget(dlg)
    return h, t, st, pm, dlg, asked, answer


def test_privacy_dialog_controls(qtbot, privacy_stack):
    h, t, st, pm, dlg, asked, answer = privacy_stack
    assert dlg.redact_box.isChecked() and not dlg.incognito_box.isChecked()
    assert (
        "offline" in dlg.flow_view.toPlainText()
        and "stays on this PC" in dlg.flow_view.toPlainText()
    )
    dlg.incognito_box.setChecked(True)
    assert st.incognito and h.suspended
    assert "incognito is ON" in dlg.flow_view.toPlainText()
    dlg.redact_box.setChecked(False)
    assert not st.redact
    assert dlg.presentation_btn.text() == "Start presentation mode"
    dlg.presentation_btn.click()
    assert pm.active and dlg.presentation_btn.text() == "End presentation mode"
    dlg.presentation_btn.click()
    assert not pm.active


def test_privacy_dialog_wipe_needs_confirmation(qtbot, privacy_stack):
    h, t, st, pm, dlg, asked, answer = privacy_stack
    h.add_turn(TurnRecord("keep me"))
    dlg.wipe_btn.click()
    assert h.counts() == (1, 1) and "cannot be undone" in asked[0]
    answer[0] = True
    dlg.wipe_btn.click()
    assert h.counts() == (0, 0)


def test_dialog_reflects_external_changes(qtbot, privacy_stack):
    _, _, st, pm, dlg, _, _ = privacy_stack
    st.set_incognito(True)  # e.g. from the tray menu
    assert dlg.incognito_box.isChecked()
    pm.activate()
    assert dlg.presentation_btn.text() == "End presentation mode"


def test_main_window_banner_and_consent_wiring(qtbot, privacy_stack, tmp_path):
    h, t, st, pm, dlg, _, _ = privacy_stack
    win = MainWindow()
    qtbot.addWidget(win)
    assert not win.privacy_button.isEnabled()
    gate = ConsentGate(
        QSettings(str(tmp_path / "c.ini"), QSettings.Format.IniFormat), lambda _t: False
    )
    win.attach_privacy(st, pm, dlg, gate)
    assert win.privacy_button.isEnabled() and not win.banner.isVisibleTo(win)
    assert win.audio_panel.before_start == gate.ensure
    st.set_incognito(True)
    assert win.banner.isVisibleTo(win) and "INCOGNITO" in win.banner.text()
    assert "7a5cf5" in win.banner.styleSheet()
    pm.activate()
    assert "PRESENTATION MODE" in win.banner.text() and "d32f2f" in win.banner.styleSheet()
    pm.deactivate()
    assert "INCOGNITO" in win.banner.text()
    st.set_incognito(False)
    assert not win.banner.isVisibleTo(win)
    win.show_privacy()
    assert dlg.isVisible()
    dlg.close()


def test_no_pii_regex_catastrophic_backtracking():
    import time

    nasty = "1" * 5000 + " " + "a" * 5000 + "@" + "b" * 5000 + " " + "( " * 2000
    t0 = time.perf_counter()
    redact(nasty)
    assert time.perf_counter() - t0 < 2.0
    assert re.search(r"\[", redact_text("x@y.com"))
