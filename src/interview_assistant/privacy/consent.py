from collections.abc import Callable

from PySide6.QtCore import QSettings

from interview_assistant.privacy.disclosure import CONSENT_TEXT

KEY = "consent/recording_v1"


class ConsentGate:
    """Asks the user to acknowledge the recording notice before the first capture.

    The acknowledgement is remembered on this PC; `required=False` skips the prompt.
    """

    def __init__(
        self,
        settings: QSettings,
        prompt: Callable[[str], bool],
        required: bool = True,
    ) -> None:
        self._settings = settings
        self._prompt = prompt
        self._required = required

    @property
    def granted(self) -> bool:
        return not self._required or bool(self._settings.value(KEY, False, type=bool))

    def ensure(self) -> bool:
        """True if capture may start (already acknowledged, or the user just agreed)."""
        if self.granted:
            return True
        if self._prompt(CONSENT_TEXT):
            self._settings.setValue(KEY, True)
            return True
        return False

    def revoke(self) -> None:
        self._settings.remove(KEY)
