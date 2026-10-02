"""Black-box hardening checks against the REAL app (source, or the packaged exe via APP_EXE).

1. a second launch exits by itself and the first keeps running
2. the log file exists, contains startup lines, and no secrets
3. after the first instance ends, a new launch works (the lock was released)
4. a corrupted history database does not prevent startup: it is set aside and the user is told

Usage: [APP_EXE=path] python scripts/live_hardening.py      (uses a throwaway DATA_DIR)"""

import ctypes
import os
import subprocess
import sys
import tempfile
import time
from ctypes import wintypes
from pathlib import Path

user32 = ctypes.windll.user32
WM_CLOSE = 0x0010
APP_EXE = os.environ.get("APP_EXE")
LAUNCH = [APP_EXE] if APP_EXE else [sys.executable, "-m", "interview_assistant"]
DATA = Path(tempfile.mkdtemp(prefix="ia-hardening-"))
ENV = {**os.environ, "DATA_DIR": str(DATA), "SKIP_ONBOARDING": "1"}
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    results.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))
    return ok


def find_windows(title: str) -> list[int]:
    found: list[int] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _lp):
        buf = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, buf, 256)
        if buf.value == title and user32.IsWindowVisible(hwnd):
            found.append(hwnd)
        return True

    user32.EnumWindows(cb, 0)
    return found


def wait_for(title: str, timeout: float = 40.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if find_windows(title):
            return True
        time.sleep(0.3)
    return False


def stop(proc: subprocess.Popen) -> None:
    proc.terminate()
    try:
        proc.wait(8)
    except subprocess.TimeoutExpired:
        proc.kill()
    # the venv launcher may leave the real interpreter behind
    time.sleep(1.0)


def kill_leftovers() -> None:
    name = "InterviewAssistant.exe" if APP_EXE else "python.exe"
    if not APP_EXE:
        return  # never kill unrelated python processes; terminate() handled ours
    subprocess.run(["taskkill", "/F", "/IM", name], capture_output=True)


def main() -> int:
    print(f"launching: {LAUNCH}\ndata dir: {DATA}")
    first = subprocess.Popen(LAUNCH, env=ENV)
    check("first instance shows its windows", wait_for("Interview Assistant"))

    # 1. second launch
    t0 = time.time()
    second = subprocess.Popen(LAUNCH, env=ENV)
    try:
        code = second.wait(30)
        check(
            "second launch exits by itself", code == 0, f"exit {code} after {time.time() - t0:.1f}s"
        )
    except subprocess.TimeoutExpired:
        second.kill()
        check("second launch exits by itself", False, "still running after 30 s")
    check("first instance is still running", first.poll() is None)
    windows = len(find_windows("Interview Assistant")) + len(
        find_windows("Interview Assistant Overlay")
    )
    check("no duplicate windows appeared", windows == 2, f"{windows} windows")

    # 2. log file
    log = DATA / "logs" / "app.log"
    text = log.read_text(encoding="utf-8") if log.exists() else ""
    check("log file was written", "Starting Interview Assistant" in text, str(log))
    check("second launch was logged", "Another instance is already running" in text)
    check("no obvious secrets in the log", "sk-" not in text and "AIza" not in text)

    # 3. lock released after exit
    stop(first)
    kill_leftovers()
    third = subprocess.Popen(LAUNCH, env=ENV)
    check("relaunch after exit works (lock released)", wait_for("Interview Assistant"))
    stop(third)
    kill_leftovers()

    # 4. corrupted history database
    (DATA / "history.db").write_bytes(b"this is not a database " * 200)
    fourth = subprocess.Popen(LAUNCH, env=ENV)
    check("app starts with a corrupted history.db", wait_for("Interview Assistant"))
    told = wait_for("History was repaired", 15)
    check("the user is told the history was repaired", told)
    for hwnd in find_windows("History was repaired"):
        user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
    time.sleep(1.0)
    check("the damaged file is kept for inspection", bool(list(DATA.glob("history.db.corrupt-*"))))
    stop(fourth)
    kill_leftovers()

    failed = [r for r in results if not r[1]]
    print(
        f"\nRESULT: {'PASS' if not failed else 'FAIL'} ({len(results) - len(failed)}/{len(results)})"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
