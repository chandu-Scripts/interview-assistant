from collections.abc import AsyncGenerator
from typing import Protocol

from interview_assistant.llm.types import LLMRequest, LLMResult, StreamEvent


class LLMProvider(Protocol):
    name: str
    default_model: str

    def stream(self, request: LLMRequest) -> AsyncGenerator[StreamEvent, None]:
        """Stream a completion. Raises LLMError; cancelling the consumer closes the connection."""
        ...


async def collect(provider: LLMProvider, request: LLMRequest) -> LLMResult:
    """Consume a stream into a single result (non-streaming convenience)."""
    result = LLMResult(provider=provider.name, model=request.model or provider.default_model)
    parts: list[str] = []
    async for ev in provider.stream(request):
        parts.append(ev.delta)
        if ev.usage:
            result.usage = ev.usage
        if ev.finish_reason:
            result.finish_reason = ev.finish_reason
    result.text = "".join(parts)
    return result
