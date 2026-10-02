"""Restart the app after settings changes.

The relaunch happens only after this process has fully exited its event loop and released its
hotkeys, so the new instance can register them again."""

import subprocess
import sys

_requested = False


def request_restart() -> None:
    global _requested
    _requested = True


def restart_requested() -> bool:
    return _requested


def relaunch() -> None:
    """Start a fresh copy of the app. Call at the very end of main()."""
    # Packaged: sys.executable is the app itself. From source: run the module.
    cmd = (
        [sys.executable]
        if getattr(sys, "frozen", False)
        else [sys.executable, "-m", "interview_assistant"]
    )
    subprocess.Popen(  # noqa: S603 - our own executable
        cmd,
        creationflags=getattr(subprocess, "DETACHED_PROCESS", 0),
        close_fds=True,
    )
