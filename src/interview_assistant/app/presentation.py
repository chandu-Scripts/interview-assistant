import logging
from collections.abc import Sequence
from typing import Protocol

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QWidget

log = logging.getLogger(__name__)


class Pausable(Protocol):
    def set_paused(self, paused: bool) -> None: ...


class Stoppable(Protocol):
    def stop(self) -> None: ...


class PresentationMode(QObject):
    """Explicit "I'm presenting" mode: hide the assistant on the user's own screen.

    Activated only by a deliberate user action (button, tray menu or hotkey) and always OFF at
    startup. While active: the overlay and history windows are hidden, the main window is
    minimized (so the taskbar can always bring it back), capture stops and no new answers are
    produced. Ending the mode restores the windows; capture is NOT resumed automatically.

    This works by hiding windows on this PC. It deliberately does not use any OS feature that
    would make windows invisible to screen-sharing software while remaining on screen.
    """

    changed = Signal(bool)

    def __init__(
        self,
        hide: list[QWidget],
        main_window: QWidget | None,
        audio: Stoppable | Sequence[Stoppable] | None = None,
        controller: object | None = None,
    ) -> None:
        super().__init__()
        self._hide = hide
        self._main = main_window
        # Step 19: a single Stoppable still works (e.g. existing single-audio-panel callers);
        # a sequence stops every simultaneous capture (mic + loopback) when presentation starts.
        self._audio: Sequence[Stoppable] = (
            [] if audio is None else audio if isinstance(audio, Sequence) else [audio]
        )
        self._controller = controller
        self._active = False
        self._was_visible: list[QWidget] = []
        self._main_was_visible = False

    @property
    def active(self) -> bool:
        return self._active

    def toggle(self) -> None:
        if self._active:
            self.deactivate()
        else:
            self.activate()

    def activate(self) -> None:
        if self._active:
            return
        self._active = True
        # Stop everything that could produce content first, then hide.
        for audio in self._audio:
            audio.stop()
        if self._controller is not None:
            stop = getattr(self._controller, "stop", None)
            if callable(stop):
                stop()
            pause = getattr(self._controller, "set_paused", None)
            if callable(pause):
                pause(True)
        self._was_visible = [w for w in self._hide if w.isVisible()]
        for w in self._was_visible:
            w.hide()
        if self._main is not None:
            self._main_was_visible = self._main.isVisible() and not self._main.isMinimized()
            if self._main_was_visible:
                self._main.showMinimized()
        log.info("Presentation mode ON")
        self.changed.emit(True)

    def deactivate(self) -> None:
        if not self._active:
            return
        self._active = False
        for w in self._was_visible:
            w.show()
        self._was_visible = []
        if self._main is not None and self._main_was_visible:
            self._main.showNormal()
        pause = getattr(self._controller, "set_paused", None)
        if callable(pause):
            pause(False)
        log.info("Presentation mode OFF")
        self.changed.emit(False)
