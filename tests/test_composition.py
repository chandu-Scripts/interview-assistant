"""The whole application assembled in-process (everything except the event loop, hotkeys, the OS
tray and the single-instance lock, which belong to main())."""

import time
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QLabel

from interview_assistant.app import restart
from interview_assistant.app.composition import AppParts, build_app
from interview_assistant.config.keys import MemoryKeyStore
from interview_assistant.config.settings import Settings
from interview_assistant.config.user_settings import UserSettingsStore
from interview_assistant.history.store import HistoryStore, TurnRecord


class FakeSTT:
    name = "fake-stt"
    metered = False

    def warmup(self):
        pass

    def transcribe(self, pcm):
        return ""


class BilledSTT(FakeSTT):
    name = "cloud-stt"
    metered = True


class StubApp:
    def __init__(self):
        self.quit_calls = 0

    def quit(self):
        self.quit_calls += 1


@pytest.fixture
def parts_factory(qtbot, tmp_path, monkeypatch):
    built: list[AppParts] = []
    monkeypatch.setattr("interview_assistant.app.composition.build_provider", lambda s: FakeSTT())
    for k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.chdir(tmp_path)  # no project .env

    def make(
        consent=lambda text: True, restart_ok=lambda w: True, qapp=None, stt=None, **overrides
    ):
        if stt is not None:
            monkeypatch.setattr("interview_assistant.app.composition.build_provider", lambda s: stt)
        settings = Settings(**{"usage_log": False, **overrides})
        app_settings = QSettings(str(tmp_path / "app.ini"), QSettings.Format.IniFormat)
        parts = build_app(
            qapp or StubApp(),
            settings,
            MemoryKeyStore(),
            app_settings,
            consent,
            restart_ok,
            UserSettingsStore(tmp_path / "settings.json"),
        )
        for w in (parts.window, parts.overlay):
            qtbot.addWidget(w)
            w.show()
        built.append(parts)
        return parts

    yield make
    for p in built:
        p.window.audio_panel.stop()
        p.controller.shutdown()
        if p.window.transcriber:
            p.window.transcriber.stop()
        p.history.close()


def test_everything_is_created_and_connected(parts_factory):
    p = parts_factory()
    assert p.window.controller is p.controller and p.window.overlay is p.overlay
    assert p.window.privacy_button.isEnabled() and p.window.settings_button.isEnabled()
    assert p.window.history_button.isEnabled() and p.window.overlay_button.isEnabled()
    assert p.window.audio_panel.before_start is not None  # the consent gate is in the path
    assert p.stt_name == "fake-stt" and p.providers == {}
    assert p.tray.presentation_action.isEnabled() and not p.presentation.active
    assert p.jev.config.redact is True and p.tracker.budget is None


def test_typed_question_reaches_jev_and_reports_a_missing_provider(qtbot, parts_factory):
    p = parts_factory()
    p.overlay.input.setText("Write a function to solve the two sum problem.")
    p.overlay.ask_btn.click()
    qtbot.waitUntil(lambda: "API key" in p.overlay.answer.toPlainText(), timeout=5000)
    qtbot.waitUntil(lambda: p.history.counts()[1] == 1, timeout=5000)
    (turn,) = p.history.get_turns(p.history.list_sessions()[0].id)
    assert turn.status == "error" and turn.source == "typed"


def test_presentation_mode_tray_and_second_launch_behaviour(qtbot, parts_factory):
    p = parts_factory()
    p.presentation.toggle()
    assert not p.overlay.isVisible() and p.tray.presentation_action.isChecked()
    assert "PRESENTATION MODE" in p.tray.tray.toolTip() and p.window.banner.isVisibleTo(p.window)
    p.toggle_overlay()  # the hotkey must not pop the overlay up while presenting
    p.on_second_launch()  # nor may a second app launch
    assert not p.overlay.isVisible() and p.window.isMinimized()
    p.presentation.toggle()
    assert p.overlay.isVisible() and not p.tray.presentation_action.isChecked()
    p.toggle_overlay()
    assert not p.overlay.isVisible()
    p.on_second_launch()  # normal second launch: bring everything to the front
    assert p.overlay.isVisible() and p.window.isVisible()


def test_incognito_from_tray_updates_everything(parts_factory):
    p = parts_factory()
    p.tray.incognito_action.trigger()
    assert p.privacy.incognito and p.history.suspended and p.tracker.persist is False
    assert "INCOGNITO" in p.tray.tray.toolTip() and p.window.banner.isVisibleTo(p.window)
    p.tray.incognito_action.trigger()
    assert not p.privacy.incognito and not p.history.suspended


def test_settings_change_offers_a_restart_and_quits_when_accepted(
    qtbot, parts_factory, monkeypatch
):
    monkeypatch.setattr(restart, "_requested", False)
    asked, app = [], StubApp()
    p = parts_factory(qapp=app, restart_ok=lambda w: asked.append(w) or True)
    p.settings_dialog.saved.emit(False)
    assert asked == [] and app.quit_calls == 0  # nothing changed: no prompt
    p.settings_dialog.saved.emit(True)
    assert asked == [p.window] and app.quit_calls == 1 and restart.restart_requested()
    monkeypatch.setattr(restart, "_requested", False)


def test_declining_the_restart_keeps_running(qtbot, parts_factory, monkeypatch):
    monkeypatch.setattr(restart, "_requested", False)
    app = StubApp()
    p = parts_factory(qapp=app, restart_ok=lambda w: False)
    p.settings_dialog.saved.emit(True)
    assert app.quit_calls == 0 and not restart.restart_requested()


def test_capture_is_blocked_until_the_recording_notice_is_accepted(qtbot, parts_factory):
    prompts = []
    p = parts_factory(consent=lambda text: prompts.append(text) or False)
    panel = p.window.audio_panel
    if not panel.toggle_button.isEnabled():
        pytest.skip("no audio devices on this machine")
    panel.toggle_button.click()
    assert not panel.recording and len(prompts) == 1 and "responsible" in prompts[0]


def test_settings_flow_into_the_running_components(parts_factory, tmp_path):
    p = parts_factory(
        budget_usd=4.0,
        budget_scope="day",
        budget_hard_stop=True,
        redact_pii=False,
        incognito=True,
        candidate_profile="Data engineer",
        hotkey_presentation="ctrl+alt+p",
        hotkey_overlay="",
    )
    assert (
        p.tracker.budget and p.tracker.budget.limit_usd == 4.0 and p.tracker.budget.scope == "day"
    )
    assert p.tracker.budget.hard_stop is True
    assert p.jev.config.redact is False and p.jev.config.profile == "Data engineer"
    assert p.privacy.incognito and p.history.suspended
    labels = " ".join(lbl.text() for lbl in p.privacy_dialog.findChildren(QLabel))
    assert "ctrl+alt+p = presentation mode" in labels  # only the configured hotkeys are advertised
    assert "show/hide overlay" not in labels  # hotkey_overlay was disabled ("")


def test_billed_speech_recognition_turns_off_partials(parts_factory):
    free = parts_factory()
    billed = parts_factory(stt=BilledSTT())
    assert free.window._partials is True and billed.window._partials is False


def test_old_history_is_purged_at_startup(qtbot, tmp_path, monkeypatch):
    from interview_assistant.cost.factory import data_dir

    settings = Settings(history_retention_days=30, usage_log=False)
    db = data_dir(settings) / "history.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    old = HistoryStore(db, clock=lambda: time.time() - 90 * 86400)
    old.add_turn(TurnRecord("ancient question"))
    old.close()
    monkeypatch.setattr("interview_assistant.app.composition.build_provider", lambda s: FakeSTT())
    ini = QSettings(str(tmp_path / "a.ini"), QSettings.Format.IniFormat)
    parts = build_app(
        StubApp(),
        settings,
        MemoryKeyStore(),
        ini,
        lambda t: True,
        lambda w: True,
        UserSettingsStore(tmp_path / "s.json"),
    )
    try:
        assert parts.history.counts() == (0, 0)
    finally:
        parts.controller.shutdown()
        if parts.window.transcriber:
            parts.window.transcriber.stop()
        parts.history.close()
        qtbot.addWidget(parts.window)
        qtbot.addWidget(parts.overlay)


def test_main_module_imports_and_exposes_entry_point():
    from interview_assistant import __main__ as entry

    assert callable(entry.main) and Path(entry.__file__).name == "__main__.py"
