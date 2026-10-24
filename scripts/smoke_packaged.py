"""Check packaged dashboard startup and single-instance handling without starting capture.

Uses an isolated data directory and only inspects/terminates processes launched by this script.
Usage: python scripts/smoke_packaged.py [path/to/InterviewAssistant.exe]
"""

import ctypes
import json
import os
import subprocess
import sys
import tempfile
import time
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def visible_windows(pid: int) -> list[str]:
    user32 = ctypes.windll.user32
    found: list[str] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd: int, _param: int) -> bool:
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd):
            title = ctypes.create_unicode_buffer(512)
            user32.GetWindowTextW(hwnd, title, len(title))
            if title.value:
                found.append(title.value)
        return True

    user32.EnumWindows(visit, 0)
    return found


def main() -> int:
    exe = (
        Path(sys.argv[1]).resolve()
        if len(sys.argv) > 1
        else (ROOT / "dist" / "InterviewAssistant" / "InterviewAssistant.exe")
    )
    (ROOT / "tmp").mkdir(exist_ok=True)
    data = Path(tempfile.mkdtemp(prefix="packaged-smoke-", dir=ROOT / "tmp"))
    env = {**os.environ, "DATA_DIR": str(data), "SKIP_ONBOARDING": "true"}
    # A native window is necessary for this check; capture still requires an explicit start.
    env.pop("QT_QPA_PLATFORM", None)
    processes: list[subprocess.Popen] = []
    report: dict[str, object] = {"executable": str(exe), "data_dir": str(data)}
    try:
        start = time.monotonic()
        first = subprocess.Popen([str(exe)], cwd=data, env=env)
        processes.append(first)
        titles: list[str] = []
        while time.monotonic() - start < 45 and first.poll() is None:
            titles = visible_windows(first.pid)
            if "Interview Assistant" in titles:
                break
            time.sleep(0.2)
        report["startup_seconds"] = round(time.monotonic() - start, 2)
        report["dashboard_visible"] = "Interview Assistant" in titles
        if not report["dashboard_visible"]:
            raise RuntimeError("Packaged dashboard did not appear within 45 seconds")
        second = subprocess.Popen([str(exe)], cwd=data, env=env)
        processes.append(second)
        report["second_instance_exit"] = second.wait(timeout=30)
        report["first_instance_alive"] = first.poll() is None
        report["ok"] = report["second_instance_exit"] == 0 and report["first_instance_alive"]
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        report.update(ok=False, error=str(error))
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
        output = data / "smoke.json"
        output.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2))
        print(f"Report: {output}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
