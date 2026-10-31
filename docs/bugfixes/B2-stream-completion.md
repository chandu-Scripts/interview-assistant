# B2: require explicit stream completion

## Problem and acceptance criteria

A successful HTTP stream ending did not prove the answer finished. The adapters accepted
EOF without a finish signal, the router recorded a successful attempt, and
`completion_is_normal(None)` let truncated text become a completed answer and Jev memory.

Acceptance: a missing terminal signal cannot produce a completed answer or conversation
memory. Keep received text and accounting; expose explicit continuation for a partial
answer. An empty stream must fail over or fail. Existing transport-error fallback,
length/max-token continuation, cancellation and ordinary successful answers must remain usable.

## Decision

Codex and Claude's parent approach review chose explicit completion validation. Each adapter
emits its finish reason only after its provider's completion contract is satisfied:

- OpenAI Chat Completions: a choice finish reason plus the `[DONE]` sentinel. Usage-only
  chunks are retained before the terminal event.
- Anthropic Messages: a `message_delta` stop reason plus `message_stop`. A later reason-only
  delta cannot erase usage received in an earlier delta.
- Gemini GenerateContent: a candidate `finishReason`, excluding `FINISH_REASON_UNSPECIFIED`.
  There is no separate `[DONE]` sentinel. Trailing usage-only events remain supported.

Missing completion after text yields the internal `incomplete` reason. The router records
exactly one failed, billable attempt and keeps the displayed text instead of automatically
paying for a replacement. A missing completion without text raises the existing network-error
type and follows the normal fallback chain. Existing raised transport errors retain their
existing failover behavior.

If a blocked, tool or other non-resumable finish reason arrived before a missing end marker,
the adapter uses `incomplete_non_resumable`. It records a failed attempt and shows an error
without enabling Continue or automatically trying another provider, even if no text arrived.

The router also checks custom providers that forget to emit a finish reason. The collector
clears per-attempt usage/reason on fallback, even if no text was shown by the previous attempt.
Jev rejects incomplete classification results and falls back to rules. Incomplete answers
do not enter Jev memory. The controller saves `incomplete`, shows Continue answer, and keeps
the exact raw prefix for explicit continuation. The overlay label is generic because a
cut-off stream and a token limit are both incomplete; history retains the specific reason.

Rejected: treating plain EOF or a usage chunk as proof of completion, silently restarting
all partially received answers, and globally changing transport-error fallback semantics.

## Validation

- `pytest tests/test_stream_completion.py tests/test_llm.py tests/test_router.py
  tests/test_continuation.py tests/test_jev.py tests/test_app.py tests/test_privacy.py
  tests/test_history.py tests/test_cost.py tests/test_answer_format.py -q`:
  **472 passed, 1 optional live test skipped** before adding three additional wire-to-UI cases.
- Additional cases exercise all three real HTTP adapters with synthetic SSE responses through
  the router, Jev, controller and Qt overlay; no live credentials or user data are used.
  Final focused rerun (`test_continuation.py` and `test_stream_completion.py`): **66 passed**.
- Adapter/router/Jev/continuation rerun after adding the non-resumable guard:
  **220 passed, 1 optional live test skipped**. Two additional empty-blocked cases were
  subsequently included in the final 66-test focused run.
- Ruff check/format and `git diff --check` pass; mypy reports no issues in 14 changed or
  directly related source modules.
- Cases cover missing reason, missing end marker, usage-only and empty EOF, normal terminal
  events, Gemini's unspecified reason, one-time failed-attempt accounting, clean-EOF explicit
  continuation, no partial memory, parseable-but-incomplete classifier JSON and fallback.
- Existing successful fake providers now explicitly emit `stop`; their assertions were not
  weakened to accept incomplete output.

## Limits and rollback

### Cancellation review follow-up

Claude's combined review identified a final-event race: Stop could invalidate an answer
after Jev yielded its terminal event but before Jev committed conversation memory. The
controller's next cancellation check happened too late. A deterministic regression failed
with a stopped history record and an unwanted completed Jev turn before this fix.

The controller now binds an acceptance callback to each request with a task-local context.
Jev uses it only at its final memory commit. The callback checks cancellation and generation
under the same short lock used by Stop and presentation invalidation, then commits under
that lock. This makes the check and append atomic without holding a lock across provider
I/O. Standalone Jev calls keep their existing behavior. Concurrent audio requests have
independent context, and Stop does not erase previously accepted conversation turns.
Shutdown also invalidates outstanding requests before stopping the runner.

Regression coverage includes Stop and presentation after the last event, Stop during
provider close, and successful answers after cancellation. The controller/Jev/continuation/
privacy/history suite passed **339 tests**; Ruff, mypy on both changed source modules, and
`git diff --check` passed. Tests use synthetic events and local Qt; no live services were used.

No live API calls were made. Protocol tests use deterministic `httpx.MockTransport` streams.
OpenAI-compatible endpoints that omit the documented end sentinel are conservatively treated
as incomplete. A normal terminal signal establishes that the provider finished generating;
it cannot establish that the answer is factually correct or substantively sufficient.

Rollback is a revert of this feature branch's commit. There is no storage schema change,
credential migration, external deployment or modification of saved Q&A.

## Primary protocol references

- [OpenAI Chat Completions streaming events](https://developers.openai.com/api/reference/resources/chat/subresources/completions/streaming-events)
- [Anthropic streaming messages](https://platform.claude.com/docs/en/build-with-claude/streaming)
- [Gemini GenerateContent response and finish reasons](https://ai.google.dev/api/generate-content)
