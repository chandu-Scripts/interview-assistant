from collections.abc import Callable

from PySide6.QtCore import QSettings

from interview_assistant.privacy.disclosure import CONSENT_TEXT

KEY = "consent/recording_v1"
SCREENSHOT_KEY = "consent/screenshot_v1"


class ConsentGate:
    """Asks the user to acknowledge a notice before the first use of a privacy-sensitive feature.

    The acknowledgement is remembered on this PC; `required=False` skips the prompt. Each feature
    has its own gate (key + notice text) so acknowledging one does not imply acknowledging another.
    """

    def __init__(
        self,
        settings: QSettings,
        prompt: Callable[[str], bool],
        required: bool = True,
        text: str = CONSENT_TEXT,
        key: str = KEY,
    ) -> None:
        self._settings = settings
        self._prompt = prompt
        self._required = required
        self._text = text
        self._key = key

    @property
    def granted(self) -> bool:
        return not self._required or bool(self._settings.value(self._key, False, type=bool))

    def ensure(self) -> bool:
        """True if the feature may run (already acknowledged, or the user just agreed)."""
        if self.granted:
            return True
        if self._prompt(self._text):
            self._settings.setValue(self._key, True)
            return True
        return False

    def revoke(self) -> None:
        self._settings.remove(self._key)
