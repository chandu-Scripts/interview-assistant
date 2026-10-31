from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal


class ErrorKind(StrEnum):
    AUTH = "auth"
    RATE_LIMIT = "rate_limit"
    BAD_REQUEST = "bad_request"
    TIMEOUT = "timeout"
    SERVER = "server"
    NETWORK = "network"
    PARSE = "parse"


# Kinds where trying the same request on another provider/model may succeed (used by the router).
RETRYABLE = {ErrorKind.RATE_LIMIT, ErrorKind.TIMEOUT, ErrorKind.SERVER, ErrorKind.NETWORK}


def output_limit_reached(reason: str | None) -> bool:
    return (reason or "").casefold() in {"length", "max_tokens"}


def completion_is_normal(reason: str | None) -> bool:
    return (reason or "").casefold() in {"stop", "end_turn", "stop_sequence"}


def completion_is_incomplete(reason: str | None) -> bool:
    return reason == "incomplete" or output_limit_reached(reason)


class LLMError(RuntimeError):
    def __init__(self, provider: str, kind: ErrorKind, message: str, status: int | None = None):
        super().__init__(f"[{provider}] {kind.value}: {message}")
        self.provider = provider
        self.kind = kind
        self.status = status

    @property
    def retryable(self) -> bool:
        return self.kind in RETRYABLE


@dataclass(frozen=True)
class Message:
    role: Literal["user", "assistant"]
    content: str


@dataclass
class LLMRequest:
    messages: list[Message]
    system: str | None = None
    model: str | None = None  # None -> provider default
    max_tokens: int = 1024
    temperature: float | None = None
    image: bytes | None = None  # PNG bytes attached to the current question only (Step 20)


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True)
class StreamEvent:
    """One streamed item: a text delta, and/or final usage / finish reason."""

    delta: str = ""
    usage: Usage | None = None
    finish_reason: str | None = None


def unfinished_stream(
    provider: str, has_text: bool, pending_reason: str | None = None
) -> StreamEvent:
    """A clean EOF is not proof of completion. Preserve partial text for explicit retry."""
    if pending_reason and not (
        completion_is_normal(pending_reason) or output_limit_reached(pending_reason)
    ):
        # A dropped sentinel must not make filtered/tool/unknown finishes resumable.
        return StreamEvent(finish_reason="incomplete_non_resumable")
    if not has_text:
        raise LLMError(provider, ErrorKind.NETWORK, "Stream ended without an answer.")
    return StreamEvent(finish_reason="incomplete")


@dataclass
class LLMResult:
    text: str = ""
    usage: Usage = field(default_factory=Usage)
    finish_reason: str | None = None
    model: str = ""
    provider: str = ""
