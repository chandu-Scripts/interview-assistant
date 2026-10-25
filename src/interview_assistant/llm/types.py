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
    return reason is None or reason.casefold() in {"stop", "end_turn", "stop_sequence"}


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


@dataclass
class LLMResult:
    text: str = ""
    usage: Usage = field(default_factory=Usage)
    finish_reason: str | None = None
    model: str = ""
    provider: str = ""
