from collections.abc import AsyncGenerator
from typing import Any

import httpx

from interview_assistant.llm.sse import (
    iter_sse,
    map_transport_error,
    parse_json,
    raise_for_status,
)
from interview_assistant.llm.types import ErrorKind, LLMError, LLMRequest, StreamEvent, Usage

URL = "https://api.anthropic.com/v1/messages"


class AnthropicProvider:
    name = "anthropic"

    def __init__(
        self,
        api_key: str,
        default_model: str = "claude-sonnet-5",
        client: httpx.AsyncClient | None = None,
        base_url: str = URL,
    ) -> None:
        self._key = api_key
        self.default_model = default_model
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(60, connect=10))
        self._url = base_url

    def build_payload(self, req: LLMRequest) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": req.model or self.default_model,
            "max_tokens": req.max_tokens,
            "messages": [{"role": m.role, "content": m.content} for m in req.messages],
            "stream": True,
        }
        if req.system:
            body["system"] = req.system
        if req.temperature is not None:
            body["temperature"] = req.temperature
        return body

    async def stream(self, request: LLMRequest) -> AsyncGenerator[StreamEvent, None]:
        input_tokens = 0
        try:
            async with self._client.stream(
                "POST",
                self._url,
                headers={"x-api-key": self._key, "anthropic-version": "2023-06-01"},
                json=self.build_payload(request),
            ) as r:
                await raise_for_status(self.name, r)
                async for event, data in iter_sse(r):
                    obj = parse_json(self.name, data)
                    kind = obj.get("type", event)
                    if kind == "message_start":
                        u = obj.get("message", {}).get("usage", {})
                        input_tokens = u.get("input_tokens", 0)
                    elif kind == "content_block_delta":
                        d = obj.get("delta", {})
                        if d.get("type") == "text_delta" and d.get("text"):
                            yield StreamEvent(delta=d["text"])
                    elif kind == "message_delta":
                        out = obj.get("usage", {}).get("output_tokens", 0)
                        stop = obj.get("delta", {}).get("stop_reason")
                        yield StreamEvent(usage=Usage(input_tokens, out), finish_reason=stop)
                    elif kind == "error":
                        e = obj.get("error", {})
                        transient = e.get("type") in ("overloaded_error", "api_error")
                        raise LLMError(
                            self.name,
                            ErrorKind.SERVER if transient else ErrorKind.BAD_REQUEST,
                            str(e.get("message", "stream error"))[:300],
                        )
                    elif kind == "message_stop":
                        return
        except httpx.HTTPError as e:
            raise map_transport_error(self.name, e) from e
