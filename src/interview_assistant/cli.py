"""Command-line switches that run without opening the UI.

    --version               print the version
    --self-test [--out F] [--deep]
                            verify bundled dependencies (JSON report; exit code 0 = ok);
                            --deep also checks HTTPS reachability and runs the Whisper model
    --purge-data            delete this PC's data folder, stored API keys and saved UI state

The packaged app is a windowed program, so output also goes to --out FILE when given (and to the
parent console when there is one)."""

import ctypes
import sys
from pathlib import Path

from interview_assistant import __version__


def _attach_console() -> None:
    """A windowed exe has no stdout; borrow the parent console if it was started from one."""
    if sys.platform == "win32" and sys.stdout is None:
        try:
            if ctypes.windll.kernel32.AttachConsole(-1):
                sys.stdout = open("CONOUT$", "w", encoding="utf-8")  # noqa: SIM115
        except OSError:
            pass


def _option(args: list[str], name: str) -> str | None:
    if name in args:
        i = args.index(name)
        if i + 1 < len(args):
            return args[i + 1]
    return None


def _emit(text: str, out: str | None) -> None:
    if out:
        Path(out).write_text(text + "\n", encoding="utf-8")
    _attach_console()
    if sys.stdout is not None:
        print(text)


def handle(argv: list[str]) -> int | None:
    """Run a CLI switch and return its exit code, or None when the UI should start."""
    args = argv[1:]
    out = _option(args, "--out")
    if "--version" in args:
        _emit(f"Interview Assistant {__version__}", out)
        return 0
    if "--self-test" in args:
        from interview_assistant import selftest

        _attach_console()
        return selftest.run(Path(out) if out else None, deep="--deep" in args)
    if "--purge-data" in args:
        from interview_assistant.purge import purge

        report = purge()
        _emit(
            f"data folder removed: {report.data_dir_removed}; keys removed: "
            f"{', '.join(report.keys_removed) or 'none'}; errors: {len(report.errors)}",
            out,
        )
        return 1 if report.errors else 0
    return None
