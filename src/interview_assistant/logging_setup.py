import logging
import sys
import threading
import traceback
from collections.abc import Callable
from logging.handlers import RotatingFileHandler
from pathlib import Path
from types import TracebackType

from interview_assistant.privacy.redact import redact_text

_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
LOG_NAME = "app.log"


class RedactingFilter(logging.Filter):
    """Masks API keys, emails, phone and card numbers in every log line, however they got there
    (exception messages, third-party libraries). Questions and answers are never logged at all."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # a broken format string must not lose the record
            message = str(record.msg)
        record.msg, record.args = redact_text(message), None
        if record.exc_info and record.exc_info[1] is not None:
            record.exc_text = redact_text("".join(traceback.format_exception(*record.exc_info)))
            record.exc_info = None
        return True


def log_dir_for(data_dir: Path) -> Path:
    return data_dir / "logs"


def setup_logging(level: str = "INFO", data_dir: Path | None = None) -> Path | None:
    """Console (when there is one) + rotating file log. Returns the log file path, if any.

    The packaged app is a windowed exe with no console, so without the file every diagnostic
    would be lost."""
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
        h.close()
    root.setLevel(level.upper())
    fmt = logging.Formatter(_FORMAT)
    handlers: list[logging.Handler] = []
    if sys.stderr is not None:
        handlers.append(logging.StreamHandler(sys.stderr))
    log_path: Path | None = None
    if data_dir is not None:
        try:
            log_dir = log_dir_for(data_dir)
            log_dir.mkdir(parents=True, exist_ok=True)
            log_path = log_dir / LOG_NAME
            handlers.append(
                RotatingFileHandler(log_path, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
            )
        except OSError:  # unwritable data dir: keep running with console logging only
            log_path = None
    for h in handlers:
        h.setFormatter(fmt)
        h.addFilter(RedactingFilter())
        root.addHandler(h)
    # Chatty libraries can echo request details; keep them at WARNING.
    for noisy in ("httpx", "httpcore", "urllib3", "huggingface_hub", "faster_whisper"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    return log_path


def install_crash_handlers(on_crash: Callable[[str], None] | None = None) -> None:
    """Log uncaught exceptions (main thread, worker threads, Qt slots) instead of losing them.

    `on_crash(summary)` lets the UI tell the user once that something went wrong."""
    log = logging.getLogger("interview_assistant.crash")
    seen: set[str] = set()

    def report(exc_type: type[BaseException], exc: BaseException, tb: TracebackType | None) -> None:
        log.error("Unhandled exception", exc_info=(exc_type, exc, tb))
        summary = f"{exc_type.__name__}: {exc}"
        if on_crash is not None and summary not in seen:
            seen.add(summary)
            try:
                on_crash(summary)
            except Exception:
                log.exception("crash callback failed")

    def excepthook(exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        report(exc_type, exc, tb)

    def thread_hook(args: threading.ExceptHookArgs) -> None:
        if args.exc_value is not None and args.exc_type is not SystemExit:
            report(args.exc_type, args.exc_value, args.exc_traceback)

    sys.excepthook = excepthook
    threading.excepthook = thread_hook
