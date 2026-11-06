"""Explicit, session-only Windows capture privacy. This is not a capture guarantee."""

import ctypes
import logging
import os
import sys
from ctypes import wintypes
from typing import Protocol

from PySide6.QtCore import QEvent, QObject, QTimer, Signal
from PySide6.QtWidgets import QApplication, QWidget

log = logging.getLogger(__name__)
NONE = 0
EXCLUDE = 0x11
Identity = tuple[int, int, str]


class AffinityBackend(Protocol):
    def supported(self) -> bool: ...
    def windows(self) -> list[int]: ...
    def identity(self, handle: int) -> Identity | None: ...
    def get(self, handle: int) -> int: ...
    def set(self, handle: int, affinity: int) -> None: ...


class WindowsAffinity:
    """Lazy native adapter; every operation is restricted to this process's root HWNDs."""

    def __init__(self) -> None:
        self._user32: ctypes.WinDLL | None = None
        self._dwm: ctypes.WinDLL | None = None

    def supported(self) -> bool:
        # Earlier Windows silently treats 0x11 as monitor-only (a black capture rectangle).
        if sys.platform != "win32" or sys.getwindowsversion().build < 19041:
            return False
        try:
            user = ctypes.WinDLL("user32", use_last_error=True)
            self._user32 = user
            self._dwm = ctypes.WinDLL("dwmapi", use_last_error=True)
            user.IsWindow.argtypes = [wintypes.HWND]
            user.IsWindow.restype = wintypes.BOOL
            user.IsWindowVisible.argtypes = [wintypes.HWND]
            user.IsWindowVisible.restype = wintypes.BOOL
            user.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
            user.GetAncestor.restype = wintypes.HWND
            user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
            user.GetWindowThreadProcessId.restype = wintypes.DWORD
            user.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
            user.GetClassNameW.restype = ctypes.c_int
            user.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
            user.SetWindowDisplayAffinity.restype = wintypes.BOOL
            user.GetWindowDisplayAffinity.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
            user.GetWindowDisplayAffinity.restype = wintypes.BOOL
            self._dwm.DwmIsCompositionEnabled.argtypes = [ctypes.POINTER(wintypes.BOOL)]
            self._dwm.DwmIsCompositionEnabled.restype = ctypes.c_long
            composing = wintypes.BOOL()
            return self._dwm.DwmIsCompositionEnabled(ctypes.byref(composing)) == 0 and bool(
                composing.value
            )
        except (OSError, AttributeError):
            return False

    def identity(self, handle: int) -> Identity | None:
        user = self._user32
        if user is None or not user.IsWindow(handle) or user.GetAncestor(handle, 2) != handle:
            return None
        pid = wintypes.DWORD()
        thread = user.GetWindowThreadProcessId(handle, ctypes.byref(pid))
        if pid.value != os.getpid():
            return None
        name = ctypes.create_unicode_buffer(256)
        if not user.GetClassNameW(handle, name, len(name)):
            return None
        return pid.value, thread, name.value

    def windows(self) -> list[int]:
        user = self._user32
        if user is None:
            raise OSError("Capture privacy is unavailable")
        handles: list[int] = []
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        @callback_type
        def collect(handle, _parameter):
            if user.IsWindowVisible(handle) and self.identity(handle) is not None:
                handles.append(int(handle))
            return True

        user.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
        user.EnumWindows.restype = wintypes.BOOL
        if not user.EnumWindows(collect, 0):
            raise OSError("Cannot enumerate application windows")
        return handles

    def get(self, handle: int) -> int:
        user = self._user32
        if user is None or self.identity(handle) is None:
            raise OSError("Application window is no longer available")
        value = wintypes.DWORD()
        if not user.GetWindowDisplayAffinity(handle, ctypes.byref(value)):
            raise OSError("Cannot verify capture privacy")
        return value.value

    def set(self, handle: int, affinity: int) -> None:
        user = self._user32
        if user is None or self.identity(handle) is None:
            raise OSError("Application window is no longer available")
        if not user.SetWindowDisplayAffinity(handle, affinity):
            raise OSError("Cannot apply capture privacy")


class CapturePrivacy(QObject):
    """Owns only display affinity, never audio, answers, visibility or presentation state."""

    changed = Signal()

    def __init__(
        self, app: QApplication, backend: AffinityBackend | None = None
    ) -> None:
        super().__init__(app)
        self._app = app
        self._backend = backend if backend is not None else WindowsAffinity()
        self._tracked: dict[int, Identity] = {}
        self._enabled = False
        self._busy = False
        self._released = False
        self.status = "Off"
        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self.audit)
        app.installEventFilter(self)

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, enabled: bool) -> None:
        if self._released or self._busy:
            return
        self._busy = True
        try:
            if not enabled:
                self._enabled = False
                self._timer.stop()
                self.status = "Off" if self._reset() else "Unavailable: reset failed; retry Off"
            elif not self._enabled:
                if not self._reset():
                    self.status = "Unavailable: reset failed; retry Off"
                else:
                    try:
                        if not self._backend.supported():
                            raise OSError("Unsupported capture privacy")
                        handles = self._backend.windows()
                        if not handles:
                            raise OSError("No visible application windows to verify")
                        self._apply(handles)
                        if not self._tracked:
                            raise OSError("No application windows could be verified")
                    except (OSError, RuntimeError):
                        self._fail()
                    else:
                        self._enabled = True
                        self.status = "On: supported capture only"
                        self._timer.start()
        finally:
            self._busy = False
        self.changed.emit()

    def _apply(self, handles: list[int]) -> None:
        for handle, tracked_identity in list(self._tracked.items()):
            if self._backend.identity(handle) != tracked_identity:
                del self._tracked[handle]
        for handle in handles:
            identity = self._backend.identity(handle)
            if identity is None:
                continue  # destroyed, child or another process: never touch it
            # Record before mutation, so partial API successes are included in rollback.
            self._tracked[handle] = identity
            if self._backend.get(handle) != EXCLUDE:
                self._backend.set(handle, EXCLUDE)
            if self._backend.get(handle) != EXCLUDE:
                raise OSError("Capture privacy readback did not match")

    def _reset(self) -> bool:
        for handle, identity in list(self._tracked.items()):
            try:
                if self._backend.identity(handle) != identity:
                    del self._tracked[handle]
                    continue  # never operate on a recycled/foreign handle
                self._backend.set(handle, NONE)
                if self._backend.get(handle) != NONE:
                    continue
            except (OSError, RuntimeError):
                continue
            del self._tracked[handle]
        return not self._tracked

    def _fail(self) -> None:
        self._enabled = False
        self._timer.stop()
        self.status = (
            "Unavailable" if self._reset() else "Unavailable: reset failed; retry Off"
        )

    def audit(self, handle: int | None = None) -> None:
        if not self._enabled or self._busy or self._released:
            return
        self._busy = True
        try:
            self._apply([handle] if handle is not None else self._backend.windows())
        except (OSError, RuntimeError):
            self._fail()
            self.changed.emit()
        finally:
            self._busy = False

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if self._enabled and not self._busy and isinstance(watched, QWidget) and watched.isWindow():
            if event.type() in (QEvent.Type.Show, QEvent.Type.WinIdChange):
                # Show is delivered before normal Qt painting. Never force native child windows.
                if event.type() == QEvent.Type.Show or watched.isVisible():
                    self._busy = True
                    try:
                        handle = int(watched.winId())
                    finally:
                        self._busy = False
                    self.audit(handle)
        return False

    def release(self) -> None:
        if self._released:
            return
        self.set_enabled(False)
        self._released = True
        self._timer.stop()
        self._app.removeEventFilter(self)
        if self._tracked:
            log.warning("Capture privacy reset was not verified during shutdown")
