"""Screen-share privacy is opt-in, truthful on failures, and independent of recording."""

import os
import sys
from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QMenu, QWidget

from interview_assistant.audio.capture import AudioCapture
from interview_assistant.privacy.capture_visibility import (
    EXCLUDE,
    NONE,
    CapturePrivacy,
    WindowsAffinity,
)
from interview_assistant.ui.audio_panel import AudioPanel
from tests.test_audio import DEVS, FakeSource
from tests.test_composition import parts_factory  # noqa: F401


class FakeAffinity:
    def __init__(self):
        self.handles = {11: (123, 1, "Qt"), 22: (123, 1, "Qt")}
        self.values = {11: NONE, 22: NONE}
        self.calls = []
        self.available = True
        self.fail_enable = set()
        self.fail_reset = set()
        self.bad_readback = set()

    def supported(self):
        self.calls.append(("supported",))
        return self.available

    def windows(self):
        self.calls.append(("windows",))
        return list(self.handles)

    def identity(self, handle):
        return self.handles.get(handle)

    def get(self, handle):
        return self.values[handle]

    def set(self, handle, value):
        self.calls.append(("set", handle, value))
        if handle in (self.fail_enable if value == EXCLUDE else self.fail_reset):
            raise OSError("Synthetic native failure")
        if handle not in self.bad_readback or value == NONE:
            self.values[handle] = value


@pytest.fixture
def privacy(qapp):
    backend = FakeAffinity()
    manager = CapturePrivacy(qapp, backend)
    yield manager, backend
    backend.fail_reset.clear()
    manager.release()


def test_off_startup_has_no_native_calls_or_polling(privacy, qtbot):
    manager, backend = privacy
    window = QWidget()
    qtbot.addWidget(window)
    window.show()
    qtbot.wait(550)
    assert manager.status == "Off" and not manager.enabled
    assert not manager._timer.isActive()
    assert backend.calls == []


def test_enable_verifies_all_windows_disable_resets(privacy):
    manager, backend = privacy
    manager.set_enabled(True)
    assert manager.enabled and manager.status == "On: supported capture only"
    assert set(backend.values.values()) == {EXCLUDE}
    assert manager._timer.isActive()
    manager.set_enabled(False)
    assert manager.status == "Off" and not manager.enabled
    assert set(backend.values.values()) == {NONE}
    assert not manager._timer.isActive()


def test_unsupported_never_claims_on_or_sets_affinity(privacy):
    manager, backend = privacy
    backend.available = False
    manager.set_enabled(True)
    assert manager.status == "Unavailable" and not manager.enabled
    assert not any(call[0] == "set" for call in backend.calls)


def test_old_windows_is_rejected_before_loading_affinity_apis(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "getwindowsversion", lambda: SimpleNamespace(build=19040))

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Old Windows must not fall back to monitor-only affinity")

    monkeypatch.setattr("ctypes.WinDLL", forbidden)
    assert not WindowsAffinity().supported()


@pytest.mark.parametrize("method", ["supported", "windows"])
def test_native_discovery_failure_is_unavailable_and_retryable(privacy, monkeypatch, method):
    manager, backend = privacy
    original = getattr(backend, method)

    def fail():
        raise OSError("Synthetic discovery failure")

    monkeypatch.setattr(backend, method, fail)
    manager.set_enabled(True)
    assert manager.status == "Unavailable" and not manager.enabled
    monkeypatch.setattr(backend, method, original)
    manager.set_enabled(True)
    assert manager.enabled


def test_no_windows_does_not_claim_verified_on(privacy):
    manager, backend = privacy
    backend.handles.clear()
    manager.set_enabled(True)
    assert manager.status == "Unavailable" and not manager.enabled


@pytest.mark.parametrize("failure", ["fail_enable", "bad_readback"])
def test_partial_enable_rolls_back_without_success_claim(privacy, failure):
    manager, backend = privacy
    getattr(backend, failure).add(22)
    manager.set_enabled(True)
    assert not manager.enabled and manager.status == "Unavailable"
    assert set(backend.values.values()) == {NONE}
    assert not manager._timer.isActive()


def test_reset_failure_keeps_unavailable_and_can_retry(privacy):
    manager, backend = privacy
    manager.set_enabled(True)
    backend.fail_reset.add(11)
    manager.set_enabled(False)
    assert not manager.enabled
    assert "reset failed" in manager.status
    assert backend.values[11] == EXCLUDE
    manager.set_enabled(True)
    assert "reset failed" in manager.status and not manager.enabled
    backend.fail_reset.clear()
    manager.set_enabled(False)
    assert manager.status == "Off" and backend.values[11] == NONE


def test_reused_handle_is_never_reset_as_previous_window(privacy):
    manager, backend = privacy
    manager.set_enabled(True)
    backend.handles[11] = (999, 9, "OtherProcess")
    backend.calls.clear()
    manager.set_enabled(False)
    assert ("set", 11, NONE) not in backend.calls
    assert manager.status == "Off"


def test_new_native_window_audit_and_recreated_handle(privacy):
    manager, backend = privacy
    manager.set_enabled(True)
    del backend.handles[11]
    backend.handles[33] = (123, 1, "Qt")
    backend.values[33] = NONE
    manager.audit()
    assert backend.values[33] == EXCLUDE
    assert 11 not in manager._tracked
    manager.set_enabled(False)
    assert backend.values[33] == NONE


def test_new_window_failure_revokes_on_and_resets_others(privacy):
    manager, backend = privacy
    manager.set_enabled(True)
    backend.handles[33] = (123, 1, "Qt")
    backend.values[33] = NONE
    backend.fail_enable.add(33)
    manager.audit()
    assert manager.status == "Unavailable" and not manager.enabled
    assert set(backend.values.values()) == {NONE}


def test_release_resets_and_rejects_late_enable(privacy):
    manager, backend = privacy
    manager.set_enabled(True)
    manager.release()
    backend.calls.clear()
    manager.set_enabled(True)
    manager.audit()
    assert backend.calls == []
    assert manager.status == "Off" and not manager.enabled


def test_qt_show_applies_before_event_returns(qapp, qtbot, monkeypatch):
    window = QWidget()
    qtbot.addWidget(window)
    backend = FakeAffinity()
    handle = int(window.winId())
    backend.handles[handle] = (123, 1, "Qt")
    backend.values[handle] = NONE
    manager = CapturePrivacy(qapp, backend)
    try:
        manager.set_enabled(True)
        backend.values[handle] = NONE
        window.show()
        assert backend.values[handle] == EXCLUDE
    finally:
        manager.release()


def test_privacy_ui_and_shutdown_leave_answering_unpaused(
    parts_factory, monkeypatch, qtbot  # noqa: F811
):
    backend = FakeAffinity()
    monkeypatch.setattr(
        "interview_assistant.privacy.capture_visibility.WindowsAffinity", lambda: backend
    )
    sources = []

    def panel(**kwargs):
        source = FakeSource()
        sources.append(source)
        return AudioPanel(
            DEVS, lambda _device, **options: AudioCapture(source, 2, 48000, **options), **kwargs
        )

    monkeypatch.setattr("interview_assistant.app.composition.AudioPanel", panel)
    parts = parts_factory()
    try:
        label = parts.overlay.findChild(QLabel, "capturePrivacyStatus")
        assert label.isHidden()
        paused = parts.controller.paused
        parts.window.audio_panel.toggle_button.click()
        assert parts.window.audio_panel.recording
        parts.privacy_dialog.capture_box.setChecked(True)
        assert parts.capture_privacy.enabled
        assert parts.controller.paused == paused
        assert not parts.presentation.active
        assert parts.window.audio_panel.recording and sources[0].stopped == 0
        assert label.text() == "Privacy On" and not label.isHidden()
        parts.overlay.resize(640, 500)
        qtbot.wait(20)
        assert parts.overlay.width() == 640
        assert label.geometry().right() <= parts.overlay.header.width()
        parts.overlay.toggle_bubble()
        qtbot.wait(20)
        assert label.isHidden()
        assert parts.overlay.width() == 48
        parts.overlay.toggle_bubble()
        qtbot.wait(20)
        assert not label.isHidden()
        parts.privacy_dialog.capture_box.setChecked(False)
        assert label.isHidden()
        parts.overlay.toggle_bubble()
        parts.overlay.toggle_bubble()
        qtbot.wait(20)
        assert label.isHidden()
        backend.available = False
        parts.privacy_dialog.capture_box.setChecked(True)
        assert label.text() == "Unavailable" and not label.isHidden()
        backend.available = True
        parts.privacy_dialog.capture_box.setChecked(True)
        assert parts.capture_privacy.enabled
        parts.presentation.activate()
        assert parts.controller.paused and not parts.window.audio_panel.recording
        assert parts.capture_privacy.enabled
        parts.shutdown()
        assert not parts.capture_privacy.enabled
        assert not parts.window.audio_panel.recording
    finally:
        parts.capture_privacy.release()


@pytest.mark.skipif(
    sys.platform != "win32" or os.environ.get("CHANDOO_NATIVE_AFFINITY_TEST") != "1",
    reason="Explicit native Windows API smoke only; no receiver/capture guarantee",
)
@pytest.mark.parametrize("layered", [False, True])
def test_native_windows_affinity_on_popup_off_and_release(qapp, qtbot, layered):
    backend = WindowsAffinity()
    assert backend.supported(), "This explicit native run needs Windows 10 2004+ and DWM"
    window = QWidget()
    window.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, layered)
    window.setWindowTitle("Synthetic screen-share privacy validation")
    window.resize(400, 200)
    qtbot.addWidget(window)
    window.show()
    qtbot.waitExposed(window)
    handle = int(window.winId())
    manager = CapturePrivacy(qapp, backend)
    popup = QMenu(window)
    popup.addAction("Synthetic menu item")
    try:
        manager.set_enabled(True)
        assert manager.enabled, manager.status
        assert window.isVisible() and backend.get(handle) == EXCLUDE
        window.setWindowFlag(Qt.WindowType.Tool, True)
        window.show()
        qtbot.waitExposed(window)
        handle = int(window.winId())
        assert manager.enabled and backend.get(handle) == EXCLUDE
        popup.popup(window.mapToGlobal(window.rect().center()))
        qtbot.waitUntil(popup.isVisible)
        popup_handle = int(popup.winId())
        assert manager.enabled, manager.status
        assert backend.get(popup_handle) == EXCLUDE
        manager.set_enabled(False)
        assert manager.status == "Off"
        assert backend.get(handle) == NONE and backend.get(popup_handle) == NONE
        manager.set_enabled(True)
        manager.release()
        assert backend.get(handle) == NONE and backend.get(popup_handle) == NONE
    finally:
        manager.release()
        popup.close()
        window.close()
