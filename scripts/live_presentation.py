"""Black-box check of presentation mode in the REAL app via the global hotkey.

Launches `python -m interview_assistant`, finds its windows through the Windows API, presses
Ctrl+Alt+P with synthetic key events, and checks what is visible. Also reads each window's display
affinity to prove nothing is excluded from screen capture (WDA_NONE == 0).
Usage: python scripts/live_presentation.py"""

import ctypes
import os
import subprocess
import sys
import time
from ctypes import wintypes

user32 = ctypes.windll.user32
KEYEVENTF_KEYUP = 0x0002
VK = {"ctrl": 0x11, "alt": 0x12, "p": 0x50, "o": 0x4F}
TITLES = {"main": "Interview Assistant", "overlay": "Interview Assistant Overlay"}


def windows_of(_pid: int) -> dict[str, int]:
    found: dict[str, int] = {}

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _lp):
        # Match by exact title: the venv launcher runs the app in a child process, so the
        # window owner is not the pid we started.
        buf = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, buf, 256)
        for key, title in TITLES.items():
            if buf.value == title:
                found[key] = hwnd
        return True

    user32.EnumWindows(cb, 0)
    return found


def state(hwnd: int) -> str:
    if user32.IsIconic(hwnd):
        return "minimized"
    return "visible" if user32.IsWindowVisible(hwnd) else "hidden"


def affinity(hwnd: int) -> int:
    a = wintypes.DWORD()
    user32.GetWindowDisplayAffinity(hwnd, ctypes.byref(a))
    return a.value


def press(*keys: str) -> None:
    for k in keys:
        user32.keybd_event(VK[k], 0, 0, 0)
    for k in reversed(keys):
        user32.keybd_event(VK[k], 0, KEYEVENTF_KEYUP, 0)


def snapshot(pid: int) -> dict[str, str]:
    w = windows_of(pid)
    return {k: state(h) for k, h in w.items()}


# Set APP_EXE to the packaged InterviewAssistant.exe to test the installed build instead of source.
APP_EXE = os.environ.get("APP_EXE")
launch = [APP_EXE] if APP_EXE else [sys.executable, "-m", "interview_assistant"]
print("launching:", launch)
t_launch = time.time()
proc = subprocess.Popen(launch)
ok = True
try:
    deadline = time.time() + 30
    while time.time() < deadline and len(windows_of(proc.pid)) < 2:
        time.sleep(0.5)
    print(f"windows appeared after {time.time() - t_launch:.1f}s")
    time.sleep(1.0)
    w = windows_of(proc.pid)
    # The venv launcher may spawn a child python; find the real pid if the first has no windows.
    print("windows found:", sorted(w))
    print("start          :", snapshot(proc.pid))
    print("display affinity (0 = capturable):", {k: affinity(h) for k, h in w.items()})
    ok &= all(affinity(h) == 0 for h in w.values())

    press("ctrl", "alt", "p")
    time.sleep(1.0)
    on = snapshot(proc.pid)
    print("after Ctrl+Alt+P:", on)
    ok &= on.get("overlay") == "hidden" and on.get("main") == "minimized"

    press("ctrl", "alt", "o")  # must be ignored while presenting
    time.sleep(0.6)
    still = snapshot(proc.pid)
    print("Ctrl+Alt+O while presenting:", still)
    ok &= still.get("overlay") == "hidden"

    press("ctrl", "alt", "p")
    time.sleep(1.0)
    off = snapshot(proc.pid)
    print("after Ctrl+Alt+P again:", off)
    ok &= off.get("overlay") == "visible" and off.get("main") == "visible"
    print("RESULT:", "PASS" if ok else "FAIL")
finally:
    proc.terminate()
    try:
        proc.wait(5)
    except subprocess.TimeoutExpired:
        proc.kill()
sys.exit(0 if ok else 1)
