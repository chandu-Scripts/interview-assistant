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
from interview_assistant.llm.types import ErrorKind, LLMError, LLMRequest, StreamEvent, Usage

BASE = "https://generativelanguage.googleapis.com/v1beta/models"


class GeminiProvider:
    name = "gemini"

    def __init__(
        self,
        api_key: str,
        default_model: str = "gemini-flash-latest",
        client: httpx.AsyncClient | None = None,
        base_url: str = BASE,
    ) -> None:
        self._key = api_key
        self.default_model = default_model
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(60, connect=10))
        self._base = base_url

    def build_payload(self, req: LLMRequest) -> dict[str, Any]:
        gen: dict[str, Any] = {"maxOutputTokens": req.max_tokens}
        if req.temperature is not None:
            gen["temperature"] = req.temperature
        contents: list[dict[str, Any]] = [
            {"role": "user" if m.role == "user" else "model", "parts": [{"text": m.content}]}
            for m in req.messages
        ]
        if req.image and contents:
            b64 = base64.b64encode(req.image).decode()
            contents[-1]["parts"].append({"inlineData": {"mimeType": "image/png", "data": b64}})
        body: dict[str, Any] = {"contents": contents, "generationConfig": gen}
        if req.system:
            body["systemInstruction"] = {"parts": [{"text": req.system}]}
        return body

    async def stream(self, request: LLMRequest) -> AsyncGenerator[StreamEvent, None]:
        model = request.model or self.default_model
        url = f"{self._base}/{model}:streamGenerateContent?alt=sse"
        try:
            async with self._client.stream(
                "POST", url, headers={"x-goog-api-key": self._key}, json=self.build_payload(request)
            ) as r:
                await raise_for_status(self.name, r)
                async for _event, data in iter_sse(r):
                    obj = parse_json(self.name, data)
                    if "error" in obj:
                        raise LLMError(
                            self.name, ErrorKind.SERVER, str(obj["error"].get("message", ""))[:300]
                        )
                    cand = (obj.get("candidates") or [{}])[0]
                    parts = (cand.get("content") or {}).get("parts") or []
                    delta = "".join(p.get("text", "") for p in parts)
                    finish = cand.get("finishReason")
                    usage = None
                    if u := obj.get("usageMetadata"):
                        # Thinking tokens are billed as output tokens.
                        out = u.get("candidatesTokenCount", 0) + u.get("thoughtsTokenCount", 0)
                        usage = Usage(u.get("promptTokenCount", 0), out)
                    if delta or finish or usage:
                        yield StreamEvent(delta, usage, finish)
        except httpx.HTTPError as e:
            raise map_transport_error(self.name, e) from e
