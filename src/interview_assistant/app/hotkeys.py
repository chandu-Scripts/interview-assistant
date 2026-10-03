"""User-registered global hotkeys (Windows RegisterHotKey), e.g. to hide the assistant instantly.

The hotkeys are explicit and documented; registering one consumes that key combination system-wide
until the app exits."""

import ctypes
import logging
import sys
from collections.abc import Callable
from ctypes import wintypes

from PySide6.QtCore import QAbstractNativeEventFilter

log = logging.getLogger(__name__)

MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000
WM_HOTKEY = 0x0312
_MODS = {
    "alt": MOD_ALT,
    "ctrl": MOD_CONTROL,
    "control": MOD_CONTROL,
    "shift": MOD_SHIFT,
    "win": MOD_WIN,
}


_NAMED_KEYS = {"enter": 0x0D, "return": 0x0D, "space": 0x20}


class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD),
        ("pt", wintypes.POINT),
    ]


def parse_hotkey(spec: str) -> tuple[int, int]:
    """'ctrl+alt+p' -> (modifiers, virtual-key). Needs at least one modifier and one key."""
    parts = [p.strip().lower() for p in spec.split("+") if p.strip()]
    if len(parts) < 2:
        raise ValueError(f"hotkey {spec!r} needs a modifier and a key, e.g. ctrl+alt+p")
    *mods, key = parts
    modifiers = 0
    for m in mods:
        if m not in _MODS:
            raise ValueError(f"unknown modifier {m!r} in {spec!r}")
        modifiers |= _MODS[m]
    if len(key) == 1 and key.isalnum():
        vk = ord(key.upper())
    elif key.startswith("f") and key[1:].isdigit() and 1 <= int(key[1:]) <= 12:
        vk = 0x70 + int(key[1:]) - 1
    elif key in _NAMED_KEYS:
        vk = _NAMED_KEYS[key]
    else:
        raise ValueError(
            f"unsupported key {key!r} in {spec!r} (use a letter, digit, F1-F12, enter or space)"
        )
    return modifiers, vk


class GlobalHotkeys(QAbstractNativeEventFilter):
    """Install with app.installNativeEventFilter(hotkeys); callbacks run on the UI thread."""

    def __init__(self) -> None:
        super().__init__()
        self._callbacks: dict[int, Callable[[], None]] = {}
        self._next_id = 1

    def register(self, spec: str, callback: Callable[[], None]) -> bool:
        """False (and a log line) if the OS refuses, e.g. another program owns the combination."""
        if sys.platform != "win32" or not spec:
            return False
        modifiers, vk = parse_hotkey(spec)
        hk_id = self._next_id
        if not ctypes.windll.user32.RegisterHotKey(None, hk_id, modifiers | MOD_NOREPEAT, vk):
            log.warning("Could not register hotkey %s (already in use?)", spec)
            return False
        self._next_id += 1
        self._callbacks[hk_id] = callback
        return True

    def unregister_all(self) -> None:
        if sys.platform == "win32":
            for hk_id in self._callbacks:
                ctypes.windll.user32.UnregisterHotKey(None, hk_id)
        self._callbacks.clear()

    def nativeEventFilter(self, eventType, message):  # noqa: N802
        if bytes(eventType) == b"windows_generic_MSG":
            msg = MSG.from_address(int(message))
            if msg.message == WM_HOTKEY:
                cb = self._callbacks.get(int(msg.wParam))
                if cb is not None:
                    try:
                        cb()
                    except Exception:
                        log.exception("Hotkey callback failed")
                    return True, 0
        return False, 0
