from PySide6.QtCore import QObject, Signal

from interview_assistant.agents.jev import JevConfig
from interview_assistant.cost.tracker import CostTracker
from interview_assistant.history.store import HistoryStore


class PrivacyState(QObject):
    """Live privacy switches. Single place that changes what is stored or sent.

    - incognito: nothing is written to history or the usage log until switched off
    - redact:    emails, phone numbers, card numbers and keys are masked in AI requests
    """

    changed = Signal()

    def __init__(
        self,
        history: HistoryStore | None,
        tracker: CostTracker | None,
        jev_config: JevConfig,
        incognito: bool = False,
        redact: bool = True,
    ) -> None:
        super().__init__()
        self._history = history
        self._tracker = tracker
        self._jev_config = jev_config
        self._incognito = False
        self.set_incognito(incognito, notify=False)
        self.set_redact(redact, notify=False)

    @property
    def incognito(self) -> bool:
        return self._incognito

    @property
    def redact(self) -> bool:
        return self._jev_config.redact

    def set_incognito(self, on: bool, notify: bool = True) -> None:
        self._incognito = on
        if self._history is not None:
            if on:
                self._history.end_session()  # what was saved so far stays a closed session
            self._history.suspended = on
        if self._tracker is not None:
            self._tracker.persist = not on
        if notify:
            self.changed.emit()

    def set_redact(self, on: bool, notify: bool = True) -> None:
        self._jev_config.redact = on
        if notify:
            self.changed.emit()

    def wipe_all(self) -> None:
        """Delete every saved question/answer and the usage log."""
        if self._history is not None:
            self._history.delete_all()
        if self._tracker is not None:
            self._tracker.wipe()
        self.changed.emit()
