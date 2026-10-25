import asyncio

import pytest
from PySide6.QtCore import QSettings

from interview_assistant.agents.jev import Jev, JevConfig
from interview_assistant.app.controller import AssistantController
from interview_assistant.cost.tracker import Budget, CostTracker
from interview_assistant.history.store import HistoryStore
from interview_assistant.llm.continuation import ContinuationText
from interview_assistant.llm.router import Route, Router, RouterConfig
from interview_assistant.llm.types import ErrorKind, LLMError, StreamEvent, Usage
from interview_assistant.ui.overlay import OverlayWindow


class Provider:
    name = "openai"

    def __init__(self, replies):
        self.replies = replies
        self.requests = []

    async def stream(self, request):
        self.requests.append(request)
        reply = self.replies[len(self.requests) - 1]
        if isinstance(reply, Exception):
            raise reply
        text, finish, *delay = reply
        for offset in range(0, len(text), 3):
            yield StreamEvent(delta=text[offset : offset + 3])
            if delay:
                await asyncio.sleep(delay[0])
        yield StreamEvent(finish_reason=finish)
        # Usage arrives after finish_reason in OpenAI's stream.
        yield StreamEvent(usage=Usage(100, 30))


@pytest.fixture
def app(qtbot, tmp_path):
    made = []

    def build(replies, format_blocks=False, redact=False):
        provider = Provider(replies)
        tracker = CostTracker()
        history = HistoryStore(tmp_path / f"history-{len(made)}.db")
        router = Router(
            {"openai": provider},
            RouterConfig(
                chains={
                    "fast": [Route("openai", "gpt-4o-mini")],
                    "smart": [Route("openai", "gpt-4o-mini")],
                }
            ),
            tracker.record_attempt,
        )
        jev = Jev(
            router,
            JevConfig(
                classify_with_llm=False,
                format_answer_blocks=format_blocks,
                redact=redact,
            ),
        )
        controller = AssistantController(jev, tracker=tracker, history=history)
        overlay = OverlayWindow(
            controller, QSettings(str(tmp_path / "overlay.ini"), QSettings.IniFormat)
        )
        qtbot.addWidget(overlay)
        overlay.show()
        made.append((controller, overlay, history))
        return controller, overlay, provider, jev, history, tracker

    yield build
    for controller, overlay, history in made:
        controller.shutdown()
        overlay.close()
        history.close()


def ask_incomplete(qtbot, controller, overlay, question="How do you optimize SQL queries?"):
    controller.ask(question)
    qtbot.waitUntil(lambda: overlay._incomplete and not overlay._busy)
    assert overlay.continue_btn.isVisible() and overlay.continue_btn.isEnabled()


@pytest.mark.parametrize("reason", ["length", "max_tokens", "MAX_TOKENS"])
def test_output_limit_is_incomplete_in_ui_history_and_memory(app, qtbot, reason):
    c, ui, provider, jev, history, tracker = app([("Check the execution plan, then", reason)])
    ask_incomplete(qtbot, c, ui)
    assert "Incomplete" in ui.origin_label.text()
    assert ui.answer_text == "Check the execution plan, then"
    assert jev.history == []
    turns = history.get_turns(history.list_sessions()[0].id)
    assert turns[0].status == "incomplete"
    assert "Output limit" in turns[0].error
    assert '"incomplete"' in history.export_all("json")
    assert turns[0].cost_usd > 0


def test_continue_appends_without_repeating_and_keeps_one_memory_turn(app, qtbot):
    prefix = "First, check the execution plan. Then look for "
    c, ui, provider, jev, history, tracker = app(
        [
            (prefix, "length"),
            (prefix + "table scans.", "stop"),
        ]
    )
    ask_incomplete(qtbot, c, ui)
    old_gen = ui._gen
    with qtbot.waitSignal(c.answer_finished):
        ui.continue_btn.click()
        c.continue_answer(old_gen)  # double-click / stale UI request cannot make a third call
    qtbot.waitUntil(lambda: len(jev.history) == 1 and not ui._busy)
    assert ui.answer_text == prefix + "table scans."
    assert not ui.continue_btn.isVisible()
    assert "Incomplete" not in ui.origin_label.text()
    assert len(provider.requests) == 2
    continuation = provider.requests[1]
    assert continuation.messages[-2].role == "assistant"
    assert continuation.messages[-2].content == prefix
    assert "Continue the interrupted answer" in continuation.messages[-1].content
    assert jev.history[0].answer == prefix + "table scans."
    turns = history.get_turns(history.list_sessions()[0].id)
    assert [t.status for t in turns] == ["incomplete", "complete"]
    assert all(t.cost_usd > 0 for t in turns)


def test_code_continuation_uses_raw_prefix_not_auto_closed_display(app, qtbot):
    prefix = "```python\nprint("
    c, ui, provider, jev, history, _ = app(
        [
            (prefix, "length"),
            ('"hello")\n```', "stop"),
        ],
        format_blocks=True,
    )
    ask_incomplete(qtbot, c, ui, "Write a Python greeting function")
    assert ui.answer_text.endswith("```")  # display closes a cut-off code block
    # Worker history may update before Qt receives the new busy/delta/finished signals.
    with qtbot.waitSignal(c.answer_finished):
        ui.continue_btn.click()
    qtbot.waitUntil(lambda: len(jev.history) == 1 and not ui._busy)
    assert provider.requests[1].messages[-2].content == prefix
    assert 'print("hello")' in ui.answer_text
    assert ui.answer_text.count("```python") == 1


def test_repeat_cutoff_can_continue_again(app, qtbot):
    c, ui, provider, jev, _, _ = app(
        [
            ("First part. ", "length"),
            ("Second part. ", "MAX_TOKENS"),
            ("Last part.", "stop"),
        ]
    )
    ask_incomplete(qtbot, c, ui)
    with qtbot.waitSignal(c.answer_incomplete):
        ui.continue_btn.click()
    qtbot.waitUntil(lambda: len(provider.requests) == 2 and ui._incomplete and not ui._busy)
    assert not jev.history
    assert ui.answer_text == "First part. Second part. "
    with qtbot.waitSignal(c.answer_finished):
        ui.continue_btn.click()
    qtbot.waitUntil(lambda: len(jev.history) == 1 and not ui._busy)
    assert ui.answer_text == "First part. Second part. Last part."


@pytest.mark.parametrize("operation", ["stop", "new_interview", "pause", "new_question"])
def test_old_continuation_cannot_run_after_invalidation(app, qtbot, operation):
    c, ui, provider, _, _, _ = app([("Partial answer", "length"), ("New answer", "stop")])
    ask_incomplete(qtbot, c, ui)
    old_gen = ui._gen
    if operation == "pause":
        c.set_paused(True)
        c.set_paused(False)
    elif operation == "new_question":
        c.ask("Explain Python lists")
        qtbot.waitUntil(lambda: len(provider.requests) == 2 and not ui._busy)
    else:
        getattr(c, operation)()
    c.continue_answer(old_gen)
    qtbot.wait(100)
    assert len(provider.requests) == (2 if operation == "new_question" else 1)


def test_stopping_continuation_preserves_prefix_and_never_marks_complete(app, qtbot):
    c, ui, provider, jev, history, _ = app(
        [
            ("Original partial answer. ", "length"),
            ("more words here", "stop", 0.1),
        ]
    )
    ask_incomplete(qtbot, c, ui)
    ui.continue_btn.click()
    qtbot.waitUntil(lambda: len(provider.requests) == 2 and ui._busy)
    c.stop()
    qtbot.waitUntil(lambda: not ui._busy)
    assert ui.answer_text.startswith("Original partial answer. ")
    assert not jev.history
    turns = history.get_turns(history.list_sessions()[0].id)
    assert [t.status for t in turns] == ["incomplete", "stopped"]


def test_failed_continuation_preserves_text_and_can_retry(app, qtbot):
    c, ui, provider, jev, history, _ = app(
        [
            ("Original partial answer. ", "length"),
            LLMError("openai", ErrorKind.NETWORK, "offline"),
            ("The rest.", "stop"),
        ]
    )
    ask_incomplete(qtbot, c, ui)
    ui.continue_btn.click()
    # The worker can append its request before Qt delivers busy/failure signals.
    # Wait for the retryable failure in the UI rather than the previous idle state.
    qtbot.waitUntil(
        lambda: (
            "continuation failed" in ui.origin_label.text()
            and not ui._busy
            and ui.continue_btn.isEnabled()
        )
    )
    assert len(provider.requests) == 2
    assert ui.continue_btn.isVisible()
    assert ui.answer_text == "Original partial answer. "
    with qtbot.waitSignal(c.answer_finished):
        ui.continue_btn.click()
    qtbot.waitUntil(lambda: len(jev.history) == 1 and not ui._busy)
    assert ui.answer_text == "Original partial answer. The rest."


def test_repeating_only_the_original_never_marks_it_complete(app, qtbot):
    prefix = "Original partial answer. "
    c, ui, provider, jev, history, _ = app([(prefix, "length"), (prefix, "stop")])
    ask_incomplete(qtbot, c, ui)
    ui.prev_qa()
    assert not ui.continue_btn.isVisible()
    ui.next_qa()
    assert ui.continue_btn.isVisible()
    ui.continue_btn.click()
    qtbot.waitUntil(lambda: len(provider.requests) == 2 and not ui._busy)
    assert ui.continue_btn.isVisible()
    assert not jev.history
    assert ui.answer_text == prefix
    assert history.get_turns(history.list_sessions()[0].id)[-1].status == "error"


def test_normal_and_blocked_finishes_do_not_offer_continuation(app, qtbot):
    c, ui, _, _, _, _ = app([("Complete answer", "stop"), ("", "content_filter")])
    c.ask("Explain SQL indexes")
    qtbot.waitUntil(lambda: ui.answer_text == "Complete answer" and not ui._busy)
    assert not ui.continue_btn.isVisible()
    c.ask("Explain another thing")
    qtbot.waitUntil(lambda: ui.chip.text() == "error" and not ui._busy)
    assert not ui.continue_btn.isVisible()


@pytest.mark.parametrize(
    "chunks,expected",
    [
        (["Original partial answer. ", "The rest."], "The rest."),
        (["Ori", "ginal partial answer. The rest."], "The rest."),
        (["New words."], "New words."),
        (["Original partial answer. "], ""),
        (["Or"], "Or"),  # partial prefix resemblance is not enough to delete content
    ],
)
def test_joiner_preserves_text_and_handles_split_repetitions(chunks, expected):
    joiner = ContinuationText("Original partial answer. ")
    assert "".join(joiner.feed(chunk) for chunk in chunks) + joiner.feed("", final=True) == expected


def test_continuation_redacts_prefix_before_sending(app, qtbot):
    c, ui, provider, jev, _, _ = app(
        [
            ("Contact engineer@example.com to", "length"),
            (" discuss this.", "stop"),
        ],
        redact=True,
    )
    ask_incomplete(qtbot, c, ui)
    ui.continue_btn.click()
    qtbot.waitUntil(lambda: len(jev.history) == 1 and not ui._busy)
    assert "engineer@example.com" not in provider.requests[1].messages[-2].content


def test_budget_blocks_extra_request_without_erasing_existing_answer(app, qtbot):
    c, ui, provider, _, history, tracker = app([("Original partial answer. ", "length")])
    ask_incomplete(qtbot, c, ui)
    tracker.budget = Budget(0.0, hard_stop=True)
    ui.continue_btn.click()
    qtbot.waitUntil(lambda: ui.chip.text() == "error" and not ui._busy)
    assert len(provider.requests) == 1
    assert ui.answer_text == "Original partial answer. "
    assert history.get_turns(history.list_sessions()[0].id)[-1].answer == "Original partial answer."


def test_provider_failover_resets_only_continuation(app, qtbot):
    prefix = "Original partial answer. "
    c, ui, provider, jev, _, _ = app([(prefix, "length")])
    ask_incomplete(qtbot, c, ui)

    class BrokenContinuation:
        name = "openai"

        async def stream(self, request):
            yield StreamEvent(delta="incorrect unfinished continuation")
            raise LLMError("openai", ErrorKind.NETWORK, "connection lost")

    backup = Provider([(prefix + "The remaining answer.", "stop")])
    jev._router._providers["openai"] = BrokenContinuation()
    jev._router._providers["backup"] = backup
    for chain in jev._router._cfg.chains.values():
        chain.append(Route("backup", "model"))
    ui.continue_btn.click()
    qtbot.waitUntil(lambda: len(jev.history) == 1 and not ui._busy)
    assert ui.answer_text == prefix + "The remaining answer."
    assert "incorrect" not in ui.answer_text


def test_screenshot_is_preserved_for_continuation(app, qtbot):
    c, ui, provider, jev, _, _ = app([("Partial screenshot answer. ", "length"), ("Rest.", "stop")])
    image = b"fake-png-for-mocked-provider"
    c.ask_screenshot(image)
    qtbot.waitUntil(lambda: ui._incomplete and not ui._busy)
    ui.continue_btn.click()
    qtbot.waitUntil(lambda: len(jev.history) == 1 and not ui._busy)
    assert all(request.image == image for request in provider.requests)


@pytest.mark.parametrize("kind", ["openai", "anthropic", "gemini"])
async def test_real_adapters_preserve_output_limit_finish_reason(kind):
    from interview_assistant.llm.anthropic_llm import AnthropicProvider
    from interview_assistant.llm.base import collect
    from interview_assistant.llm.gemini_llm import GeminiProvider
    from interview_assistant.llm.openai_llm import OpenAIProvider
    from interview_assistant.llm.types import LLMRequest, Message, output_limit_reached
    from tests.test_llm import client, sse

    if kind == "openai":
        response = sse(
            (None, {"choices": [{"delta": {"content": "Partial"}, "finish_reason": "length"}]}),
            (None, {"choices": [], "usage": {"completion_tokens": 10}}),
            (None, "[DONE]"),
        )
        provider_type = OpenAIProvider
    elif kind == "anthropic":
        response = sse(
            ("content_block_delta", {"delta": {"type": "text_delta", "text": "Partial"}}),
            (
                "message_delta",
                {"delta": {"stop_reason": "max_tokens"}, "usage": {"output_tokens": 10}},
            ),
            ("message_stop", {}),
        )
        provider_type = AnthropicProvider
    else:
        response = sse(
            (
                None,
                {
                    "candidates": [
                        {
                            "content": {"parts": [{"text": "Partial"}]},
                            "finishReason": "MAX_TOKENS",
                        }
                    ],
                    "usageMetadata": {"candidatesTokenCount": 10},
                },
            )
        )
        provider_type = GeminiProvider
    async with client(lambda request: response) as transport:
        result = await collect(
            provider_type("fake-key", client=transport), LLMRequest([Message("user", "Explain")])
        )
    assert result.text == "Partial"
    assert result.usage.output_tokens == 10
    assert output_limit_reached(result.finish_reason)
