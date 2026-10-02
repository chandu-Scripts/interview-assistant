import threading
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, Signal

Runner = Callable[[Callable[[], Any], Callable[[Any], None]], None]


class _Bridge(QObject):
    done = Signal(object)


_alive: set[_Bridge] = set()


def run_in_thread(fn: Callable[[], Any], on_done: Callable[[Any], None]) -> None:
    """Run fn on a worker thread; on_done(result_or_exception) runs on the UI thread."""
    bridge = _Bridge()
    _alive.add(bridge)

    def finish(result: Any) -> None:
        _alive.discard(bridge)
        on_done(result)

    bridge.done.connect(finish)

    def work() -> None:
        try:
            result: Any = fn()
        except Exception as e:  # reported to the UI instead of dying silently
            result = e
        bridge.done.emit(result)

    threading.Thread(target=work, daemon=True, name="ui-bg").start()


def run_inline(fn: Callable[[], Any], on_done: Callable[[Any], None]) -> None:
    """Synchronous runner (tests)."""
    try:
        result: Any = fn()
    except Exception as e:
        result = e
    on_done(result)
