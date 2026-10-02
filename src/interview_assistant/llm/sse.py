import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from interview_assistant.llm.types import ErrorKind, LLMError

_STATUS_KIND = {
    400: ErrorKind.BAD_REQUEST,
    401: ErrorKind.AUTH,
    403: ErrorKind.AUTH,
    404: ErrorKind.BAD_REQUEST,
    408: ErrorKind.TIMEOUT,
    422: ErrorKind.BAD_REQUEST,
    429: ErrorKind.RATE_LIMIT,
}


def kind_for_status(status: int) -> ErrorKind:
    if status in _STATUS_KIND:
        return _STATUS_KIND[status]
    return ErrorKind.SERVER if status >= 500 else ErrorKind.BAD_REQUEST


async def raise_for_status(provider: str, r: httpx.Response) -> None:
    if r.status_code < 400:
        return
    body = (await r.aread()).decode("utf-8", "replace")[:300]
    raise LLMError(
        provider, kind_for_status(r.status_code), f"HTTP {r.status_code}: {body}", r.status_code
    )


async def iter_sse(r: httpx.Response) -> AsyncIterator[tuple[str, str]]:
    """Yield (event, data) pairs from a text/event-stream response."""
    event: str = "message"
    data: list[str] = []
    async for line in r.aiter_lines():
        if line == "":
            if data:
                yield event, "\n".join(data)
            event, data = "message", []
        elif line.startswith(":"):
            continue
        elif line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].lstrip())
    if data:
        yield event, "\n".join(data)


def parse_json(provider: str, data: str) -> dict[str, Any]:
    try:
        obj = json.loads(data)
    except ValueError as e:
        raise LLMError(provider, ErrorKind.PARSE, "invalid JSON in stream") from e
    if not isinstance(obj, dict):
        raise LLMError(provider, ErrorKind.PARSE, "unexpected stream payload")
    return obj


def map_transport_error(provider: str, e: httpx.HTTPError) -> LLMError:
    if isinstance(e, httpx.TimeoutException):
        return LLMError(provider, ErrorKind.TIMEOUT, type(e).__name__)
    return LLMError(provider, ErrorKind.NETWORK, type(e).__name__)
