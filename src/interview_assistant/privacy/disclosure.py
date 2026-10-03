from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class DataFlow:
    what: str
    where: str
    leaves_pc: bool

    def line(self) -> str:
        arrow = "LEAVES THIS PC" if self.leaves_pc else "stays on this PC"
        return f"- {self.what} -> {self.where} [{arrow}]"


CONSENT_TEXT = (
    "This app can capture system audio and your microphone, which may include other people's "
    "voices.\n\n"
    "Only use it where everyone in the conversation has agreed to be recorded or transcribed, "
    "and where that is allowed by law and by the rules of your interview, employer or school. "
    "You are responsible for complying with them.\n\n"
    "Capture only starts when you press Start capture, and a red RECORDING indicator is shown "
    "the whole time it is running."
)


SCREENSHOT_CONSENT_TEXT = (
    "A screenshot captures everything currently visible on your screen - not just the interview "
    "app. It is sent to your AI provider along with your next question.\n\n"
    "Before you use it, make sure nothing private is on screen (other windows, messages, "
    "personal or confidential information). It only happens when you press the Screenshot "
    "button or Ctrl+Shift+S, and only the next question carries it."
)


def describe_data_flow(
    stt_provider: str,
    llm_providers: Iterable[str],
    profile_set: bool,
    redaction_on: bool,
    history_saved: bool,
    incognito: bool,
    data_path: str,
) -> list[DataFlow]:
    """Plain-language list of where the user's data goes, for the privacy dialog."""
    llms = sorted(llm_providers)
    cloud_stt = stt_provider != "local-whisper"
    flows = [
        DataFlow(
            "Microphone / system audio",
            f"{stt_provider} speech-to-text" if cloud_stt else "local Whisper model (offline)",
            cloud_stt,
        )
    ]
    if llms:
        detail = "Questions, recent conversation and any screenshot you attach"
        if profile_set:
            detail += " + your candidate profile"
        if redaction_on:
            detail += " (emails, phone numbers, card numbers and keys masked first)"
        flows.append(DataFlow(detail, "AI providers: " + ", ".join(llms), True))
    else:
        flows.append(DataFlow("Questions", "no AI provider configured - nothing is sent", False))
    if incognito:
        flows.append(DataFlow("History and usage log", "not saved (incognito is ON)", False))
    elif history_saved:
        flows.append(DataFlow("Questions, answers and usage log", data_path, False))
    else:
        flows.append(DataFlow("History", "not saved (history saving is OFF)", False))
    flows.append(DataFlow("API keys", "read from .env / environment; never logged or saved", False))
    return flows
