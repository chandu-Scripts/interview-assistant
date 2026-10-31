"""Clean HTTP EOF is not equivalent to an AI answer completing."""

import pytest

from interview_assistant.agents.jev import Jev, JevConfig
from interview_assistant.llm.anthropic_llm import AnthropicProvider
from interview_assistant.llm.base import collect
from interview_assistant.llm.gemini_llm import GeminiProvider
from interview_assistant.llm.openai_llm import OpenAIProvider
from interview_assistant.llm.router import (
    AllRoutesFailed,
    Route,
    Router,
    RouterConfig,
    collect_routed,
)
from interview_assistant.llm.types import (
    ErrorKind,
    LLMError,
    LLMRequest,
    Message,
    StreamEvent,
    Usage,
    completion_is_normal,
)
from tests.test_llm import client, sse

KINDS = {"openai": OpenAIProvider, "anthropic": AnthropicProvider, "gemini": GeminiProvider}
REQ = LLMRequest([Message("user", "Explain SQL indexes")])


def response(kind, *, text="A partial answer", finish=None, sentinel=False):
    events = []
    if kind == "openai":
        events.append((None, {"choices": [{"delta": {"content": text}}]}))
        if finish:
            events.append((None, {"choices": [{"finish_reason": finish}]}))
        events.append(
            (None, {"choices": [], "usage": {"prompt_tokens": 12, "completion_tokens": 5}})
        )
        if sentinel:
            events.append((None, "[DONE]"))
    elif kind == "anthropic":
        events.extend(
            [
                ("message_start", {"message": {"usage": {"input_tokens": 12}}}),
                ("content_block_delta", {"delta": {"type": "text_delta", "text": text}}),
                ("message_delta", {"usage": {"output_tokens": 5}}),
            ]
        )
        if finish:
            # A final reason-only message_delta must not erase previously reported usage.
            events.append(("message_delta", {"delta": {"stop_reason": finish}}))
        if sentinel:
            events.append(("message_stop", {}))
    else:
        events.append((None, {"candidates": [{"content": {"parts": [{"text": text}]}}]}))
        if finish:
            events.append((None, {"candidates": [{"finishReason": finish}]}))
        events.append(
            (None, {"usageMetadata": {"promptTokenCount": 12, "candidatesTokenCount": 5}})
        )
    return sse(*events)


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("sentinel", [False, True])
async def test_missing_finish_reason_is_incomplete_even_with_usage_or_end_marker(kind, sentinel):
    async with client(lambda req: response(kind, sentinel=sentinel)) as transport:
        result = await collect(KINDS[kind]("fake", client=transport), REQ)
    assert result.text == "A partial answer"
    assert result.usage == Usage(12, 5)
    assert result.finish_reason == "incomplete"
    assert not completion_is_normal(result.finish_reason)


@pytest.mark.parametrize("kind,finish", [("openai", "stop"), ("anthropic", "end_turn")])
async def test_finish_without_required_transport_end_marker_is_still_incomplete(kind, finish):
    async with client(lambda req: response(kind, finish=finish)) as transport:
        result = await collect(KINDS[kind]("fake", client=transport), REQ)
    assert result.finish_reason == "incomplete" and result.usage == Usage(12, 5)


@pytest.mark.parametrize("kind,finish", [("openai", "content_filter"), ("anthropic", "refusal")])
@pytest.mark.parametrize("text", ["Blocked partial", ""])
async def test_blocked_finish_without_transport_end_cannot_be_continued(kind, finish, text):
    records = []
    async with client(lambda req: response(kind, finish=finish, text=text)) as transport:
        provider = KINDS[kind]("fake", client=transport)
        router = Router(
            {kind: provider},
            RouterConfig(chains={"fast": [Route(kind, "m")]}),
            records.append,
        )
        result = await collect_routed(router, REQ)
    assert result.finish_reason == "incomplete_non_resumable"
    assert len(records) == 1 and not records[0].ok


@pytest.mark.parametrize(
    "kind,finish", [("openai", "stop"), ("anthropic", "end_turn"), ("gemini", "STOP")]
)
async def test_explicit_completion_keeps_usage_and_answer(kind, finish):
    async with client(lambda req: response(kind, finish=finish, sentinel=True)) as transport:
        result = await collect(KINDS[kind]("fake", client=transport), REQ)
    assert result.finish_reason == finish
    assert result.usage == Usage(12, 5)
    assert completion_is_normal(result.finish_reason)


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("text", ["", "   \n"])
async def test_empty_or_usage_only_eof_is_error(kind, text):
    async with client(lambda req: response(kind, text=text, sentinel=True)) as transport:
        with pytest.raises(LLMError) as error:
            await collect(KINDS[kind]("fake", client=transport), REQ)
    assert error.value.kind == ErrorKind.NETWORK


async def test_gemini_unspecified_reason_does_not_confirm_completion():
    async with client(
        lambda req: response("gemini", finish="FINISH_REASON_UNSPECIFIED")
    ) as transport:
        result = await collect(GeminiProvider("fake", client=transport), REQ)
    assert result.finish_reason == "incomplete"


@pytest.mark.parametrize("kind", KINDS)
async def test_truncated_adapter_result_never_enters_memory_or_records_success(kind):
    records = []
    async with client(lambda req: response(kind)) as transport:
        provider = KINDS[kind]("fake", client=transport)
        route = Route(kind, "synthetic-model")
        router = Router(
            {kind: provider},
            RouterConfig(chains={"fast": [route], "smart": [route]}),
            records.append,
        )
        jev = Jev(router, JevConfig(classify_with_llm=False))
        events = [event async for event in jev.ask("Explain SQL indexes")]
    assert jev.history == []
    assert events[-1].event.finish_reason == "incomplete"
    assert len(records) == 1 and not records[0].ok
    assert records[0].usage == Usage(12, 5)
    assert records[0].output_chars == len("A partial answer")
    assert records[0].error.kind == ErrorKind.NETWORK


class Script:
    name = "custom"
    default_model = "synthetic-model"

    def __init__(self, events):
        self.events = events
        self.calls = 0

    async def stream(self, request):
        self.calls += 1
        for event in self.events:
            if isinstance(event, Exception):
                raise event
            yield event


@pytest.mark.parametrize("events", [[], [StreamEvent(usage=Usage(4, 1))]])
async def test_empty_custom_provider_falls_back_and_records_failed_attempt(events):
    empty = Script(events)
    fallback = Script([StreamEvent(delta="Recovered", finish_reason="stop")])
    records = []
    router = Router(
        {"empty": empty, "fallback": fallback},
        RouterConfig(chains={"fast": [Route("empty", "m"), Route("fallback", "m")]}),
        records.append,
    )
    result = await collect_routed(router, REQ)
    assert result.text == "Recovered" and result.finish_reason == "stop"
    assert result.usage == Usage()  # usage from the failed route is not the final answer's usage
    assert [record.ok for record in records] == [False, True]
    assert empty.calls == fallback.calls == 1


async def test_empty_eof_without_fallback_is_an_error_not_success():
    router = Router({"empty": Script([])}, RouterConfig(chains={"fast": [Route("empty", "m")]}))
    with pytest.raises(AllRoutesFailed):
        await collect_routed(router, REQ)


async def test_custom_partial_eof_preserves_text_without_automatic_restart():
    partial = Script([StreamEvent(delta="Unfinished "), StreamEvent(usage=Usage(4, 2))])
    fallback = Script([StreamEvent(delta="Replacement", finish_reason="stop")])
    records = []
    router = Router(
        {"partial": partial, "fallback": fallback},
        RouterConfig(chains={"fast": [Route("partial", "m"), Route("fallback", "m")]}),
        records.append,
    )
    result = await collect_routed(router, REQ)
    assert result.text == "Unfinished " and result.finish_reason == "incomplete"
    assert result.usage == Usage(4, 2)
    assert fallback.calls == 0
    assert len(records) == 1 and not records[0].ok


@pytest.mark.parametrize("reason", [None, "", "incomplete", "SAFETY", "content_filter"])
def test_absent_or_non_normal_reason_is_never_complete(reason):
    assert not completion_is_normal(reason)


async def test_incomplete_classification_uses_rules_even_if_json_is_parseable():
    provider = Script([StreamEvent(delta='{"category": "hr", "confidence": 0.99}')])
    router = Router(
        {"custom": provider},
        RouterConfig(chains={"fast": [Route("custom", "m")]}),
    )
    jev = Jev(router, JevConfig(classify_with_llm=True))
    decision = await jev.decide("What can you tell me about this?")
    assert provider.calls == 1
    assert decision.source == "fallback"


async def test_non_streaming_collect_defends_against_custom_provider_missing_finish():
    result = await collect(Script([StreamEvent(delta="Partial")]), REQ)
    assert result.text == "Partial" and result.finish_reason == "incomplete"
