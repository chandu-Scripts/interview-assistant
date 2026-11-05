import base64
from collections.abc import AsyncGenerator
from typing import Any

import httpx

from interview_assistant.llm.sse import (
    iter_sse,
    map_transport_error,
    parse_json,
    raise_for_status,
)
from interview_assistant.llm.types import (
    ErrorKind,
    LLMError,
    LLMRequest,
    StreamEvent,
    Usage,
    unfinished_stream,
)

URL = "https://api.openai.com/v1/chat/completions"
REASONING_EFFORT = {
    "gpt-6-astra": "low",
    "gpt-6.1-sol": "low",
    "gpt-6.1-terra": "none",
    "gpt-6-luna": "none",
}


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
        msgs: list[dict[str, Any]] = []
        if req.system:
            msgs.append({"role": "system", "content": req.system})
        msgs += [{"role": m.role, "content": m.content} for m in req.messages]
        if req.image and msgs:
            text = msgs[-1]["content"]
            b64 = base64.b64encode(req.image).decode()
            msgs[-1] = {
                "role": msgs[-1]["role"],
                "content": [
                    {"type": "text", "text": text},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                ],
            }
        body: dict[str, Any] = {
            "model": req.model or self.default_model,
            "messages": msgs,
            "stream": True,
            "stream_options": {"include_usage": True},
            "max_completion_tokens": req.max_tokens,
        }
        model = body["model"]
        if model in REASONING_EFFORT:
            # Astra/Sol cannot disable reasoning. Preserve the caller's completion
            # budget (including reasoning), and avoid unsupported sampling settings.
            body["reasoning_effort"] = REASONING_EFFORT[model]
        elif model == "gpt-5.4-mini" or model.startswith("gpt-5.4-mini-"):
            # Explicitly disable reasoning for interactive interview response latency.
            body["reasoning_effort"] = "none"
        elif req.temperature is not None:
            body["temperature"] = req.temperature
        return body

    async def stream(self, request: LLMRequest) -> AsyncGenerator[StreamEvent, None]:
        finish_reason: str | None = None
        has_text = False
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
                        # finish_reason describes the choice; [DONE] closes the SSE stream.
                        yield (
                            StreamEvent(finish_reason=finish_reason)
                            if finish_reason
                            else unfinished_stream(self.name, has_text)
                        )
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
                    if finish:
                        finish_reason = finish
                    has_text = has_text or bool(delta.strip())
                    if delta or usage:
                        yield StreamEvent(delta, usage)
                yield unfinished_stream(self.name, has_text, finish_reason)
        except httpx.HTTPError as e:
            raise map_transport_error(self.name, e) from e
