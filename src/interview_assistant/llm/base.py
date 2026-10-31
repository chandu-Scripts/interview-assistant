from collections.abc import AsyncGenerator
from typing import Protocol

from interview_assistant.llm.types import LLMRequest, LLMResult, StreamEvent, unfinished_stream


class LLMProvider(Protocol):
    name: str
    default_model: str

    def stream(self, request: LLMRequest) -> AsyncGenerator[StreamEvent, None]:
        """Emit an explicit finish_reason only after the provider's terminal signal.

        A clean EOF without that signal emits 'incomplete' if text exists, otherwise
        raises LLMError. Cancellation closes the connection; transport errors raise LLMError.
        """
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
    if result.finish_reason is None:
        result.finish_reason = unfinished_stream(
            provider.name, bool(result.text.strip())
        ).finish_reason
    return result
