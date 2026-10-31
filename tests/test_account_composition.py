"""Account-scoped desktop composition with no capture, network or OS credentials."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication

from interview_assistant.account import context as context_module
from interview_assistant.account.context import AccountContext
from interview_assistant.app import composition
from interview_assistant.config.keys import MemoryKeyStore
from interview_assistant.config.relay import MemoryRelayTokenStore
from interview_assistant.config.settings import Settings
from interview_assistant.config.user_settings import UserSettingsStore
from interview_assistant.history.store import HistoryStore, TurnRecord

USER = "11111111-1111-4111-8111-111111111111"


class FakeSTT:
    name = "fake-offline-stt"
    metered = False

    def warmup(self):
        pass

    def transcribe(self, pcm):
        pytest.fail("Composition tests must never capture or transcribe audio")


class FakeTray:
    """Keep callbacks inspectable without creating a native tray icon."""

    def __init__(self, overlay, presentation, incognito, open_main, quit_app):
        self.overlay = overlay
        self.presentation = presentation
        self.incognito = incognito
        self.open_main = open_main
        self.quit_app = quit_app
        self.hidden = False
        self.states = []

    def set_state(self, presentation, incognito):
        self.states.append((presentation, incognito))

    def hide(self):
        self.hidden = True


@pytest.fixture
def scoped_parts(qtbot, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    root = tmp_path / "data"
    root.mkdir()
    legacy = {
        "settings.json": b'{"candidate_profile": "legacy private resume"}',
        "resumes.json": b'[{"name":"legacy.txt","text":"legacy","last_used":1}]',
        "history.db": b"unowned legacy database must never be opened or quarantined",
        "practice_qa.json": b"unowned practice bank",
    }
    for name, content in legacy.items():
        (root / name).write_bytes(content)
    context = AccountContext(root, "https://composition-test.supabase.co", USER)
    monkeypatch.setattr(context_module, "_context", context)
    monkeypatch.setenv("DATA_DIR", str(root))
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-global-key-must-not-load")
    monkeypatch.setattr(composition, "build_provider", lambda _: FakeSTT())
    monkeypatch.setattr(composition, "build_preview_provider", lambda _: FakeSTT())
    monkeypatch.setattr(composition, "build_providers", lambda _: {})
    monkeypatch.setattr(composition, "TrayController", FakeTray)
    monkeypatch.setattr("interview_assistant.ui.audio_panel.list_devices", lambda: [])
    monkeypatch.setattr(
        "interview_assistant.app.practice_matcher.PracticeMatcher.prepare", lambda _: None
    )
    monkeypatch.setattr(
        "interview_assistant.ui.settings_dialog.KeyringRelayTokenStore", MemoryRelayTokenStore
    )
    opened_history = []

    def history_factory(path, **kwargs):
        opened_history.append(Path(path))
        return HistoryStore(path, **kwargs)

    monkeypatch.setattr(composition, "HistoryStore", history_factory)
    settings = Settings(history_retention_days=1, usage_log=True)
    store = UserSettingsStore()
    ini = context.qsettings("App")
    parts = composition.build_app(
        QApplication.instance(),
        settings,
        MemoryKeyStore(),
        ini,
        lambda _: pytest.fail("No capture consent should be requested"),
        lambda _: False,
        store,
    )
    for widget in (parts.window, parts.overlay, parts.start_screen):
        qtbot.addWidget(widget)
    result = SimpleNamespace(
        parts=parts,
        context=context,
        root=root,
        legacy=legacy,
        opened_history=opened_history,
        store=store,
        ini=ini,
    )
    try:
        yield result
    finally:
        context.revoke()
        parts.shutdown()
        # shutdown owns history.close; calling it again here masks lifetime bugs.


def snapshot(directory):
    return {
        str(path.relative_to(directory)): path.read_bytes()
        for path in directory.rglob("*")
        if path.is_file()
    }


def assert_legacy_unchanged(built):
    for name, content in built.legacy.items():
        assert (built.root / name).read_bytes() == content
    assert not list(built.root.glob("*.corrupt*"))
    assert not list(built.root.glob("*.bad"))


def test_composition_opens_only_verified_account_stores(scoped_parts):
    built = scoped_parts
    parts = built.parts
    account = built.context.account_dir
    assert built.opened_history == [account / "history.db"]
    assert parts.history.counts() == (0, 0)
    assert parts.settings.candidate_profile is None
    assert parts.settings.openai_api_key is None
    assert parts.start_screen.resume_page.library._path == account / "resumes.json"
    assert parts.start_screen.resume_page.library.list() == []
    practice = parts.start_screen._workspace_widgets["practice"].library
    assert practice.path == account / "practice_qa.json"
    assert practice.entries() == []
    assert built.store.path == account / "settings.json"
    assert Path(built.ini.fileName()) == account / "App.ini"
    assert parts.window.audio_panel.recording is False
    assert parts.window.mic_panel.recording is False
    assert_legacy_unchanged(built)


def test_revoked_shutdown_is_idempotent_and_closes_every_surface(scoped_parts, qtbot):
    parts = scoped_parts.parts
    parts.open_main()
    parts.toggle_overlay()
    parts.window.show_settings()
    assert parts.start_screen.isVisible()
    assert parts.settings_dialog.isVisible()
    scoped_parts.context.revoke()
    parts.shutdown()
    parts.shutdown()
    QApplication.processEvents()
    assert parts.tray.hidden
    assert not parts.start_screen.isVisible()
    assert not parts.overlay.isVisible()
    assert not parts.window.isVisible()
    assert not parts.settings_dialog.isVisible()
    assert not parts.privacy_dialog.isVisible()
    assert parts.history.suspended
    assert not parts.tracker.persist
    assert_legacy_unchanged(scoped_parts)


def test_retained_window_callbacks_cannot_reopen_revoked_workspace(scoped_parts):
    parts = scoped_parts.parts
    parts.open_main()
    scoped_parts.context.revoke()
    parts.shutdown()
    for callback in (
        parts.open_main,
        parts.on_second_launch,
        parts.toggle_overlay,
        parts.tray.open_main,
        parts.tray.overlay,
        parts.window.show_settings,
        parts.window.show_history,
        parts.window.show_privacy,
    ):
        callback()
    QApplication.processEvents()
    assert not parts.start_screen.isVisible()
    assert not parts.overlay.isVisible()
    assert not parts.window.isVisible()


def test_queued_presentation_exit_cannot_restore_revoked_windows(scoped_parts):
    parts = scoped_parts.parts
    parts.open_main()
    parts.toggle_overlay()
    parts.presentation.activate()
    assert parts.presentation.active
    scoped_parts.context.revoke()
    parts.shutdown()
    parts.tray.presentation()
    QApplication.processEvents()
    assert not parts.start_screen.isVisible()
    assert not parts.overlay.isVisible()


def test_queued_incognito_toggle_cannot_reenable_persistence_after_logout(scoped_parts):
    parts = scoped_parts.parts
    parts.privacy.set_incognito(True)
    scoped_parts.context.revoke()
    parts.shutdown()
    parts.tray.incognito()
    assert parts.history.suspended
    assert not parts.tracker.persist


def test_late_persistence_callbacks_cannot_change_account_files(scoped_parts):
    built = scoped_parts
    parts = built.parts
    built.store.save({"answer_mode": "manual"})
    built.ini.setValue("test", "original")
    parts.history.add_turn(TurnRecord("Synthetic initial question"))
    parts.start_screen.resume_page.library.add("synthetic.txt", "Synthetic candidate")
    practice = parts.start_screen._workspace_widgets["practice"].library
    practice.save(None, "Synthetic question?", "Synthetic answer")
    built.context.revoke()
    parts.shutdown()
    before = snapshot(built.root)
    assert parts.history.add_turn(TurnRecord("Late question")) is None
    parts.tracker.record_stt("fake", "fake", 3.0)
    parts.overlay._save_geometry()
    with pytest.raises(PermissionError):
        built.store.save({"answer_mode": "auto"})
    with pytest.raises(PermissionError):
        built.ini.setValue("test", "late mutation")
    with pytest.raises(ValueError):
        parts.start_screen.resume_page.library.add("late.txt", "Late candidate")
    with pytest.raises(ValueError):
        practice.save(None, "Late question?", "Late answer")
    QApplication.processEvents()
    assert snapshot(built.root) == before
    assert_legacy_unchanged(built)
