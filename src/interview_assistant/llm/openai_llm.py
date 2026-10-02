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

URL = "https://api.openai.com/v1/chat/completions"


class OpenAIProvider:
    name = "openai"

    def __init__(
        self,
        api_key: str,
        default_model: str = "gpt-4o-mini",
        client: httpx.AsyncClient | None = None,
        base_url: str = URL,
    ) -> None:
        self._key = api_key
        self.default_model = default_model
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(60, connect=10))
        self._url = base_url

    def build_payload(self, req: LLMRequest) -> dict[str, Any]:
        msgs: list[dict[str, str]] = []
        if req.system:
            msgs.append({"role": "system", "content": req.system})
        msgs += [{"role": m.role, "content": m.content} for m in req.messages]
        body: dict[str, Any] = {
            "model": req.model or self.default_model,
            "messages": msgs,
            "stream": True,
            "stream_options": {"include_usage": True},
            "max_completion_tokens": req.max_tokens,
        }
        if req.temperature is not None:
            body["temperature"] = req.temperature
        return body

    async def stream(self, request: LLMRequest) -> AsyncGenerator[StreamEvent, None]:
        try:
            async with self._client.stream(
                "POST",
                self._url,
                headers={"Authorization": f"Bearer {self._key}"},
                json=self.build_payload(request),
            ) as r:
                await raise_for_status(self.name, r)
                async for _event, data in iter_sse(r):
                    if data.strip() == "[DONE]":
                        return
                    obj = parse_json(self.name, data)
                    if "error" in obj:
                        msg = str(obj["error"].get("message", "stream error"))[:300]
                        raise LLMError(self.name, ErrorKind.SERVER, msg)
                    usage = None
                    if u := obj.get("usage"):
                        usage = Usage(u.get("prompt_tokens", 0), u.get("completion_tokens", 0))
                    choice = (obj.get("choices") or [{}])[0]
                    delta = (choice.get("delta") or {}).get("content") or ""
                    finish = choice.get("finish_reason")
                    if delta or usage or finish:
                        yield StreamEvent(delta, usage, finish)
        except httpx.HTTPError as e:
            raise map_transport_error(self.name, e) from e
