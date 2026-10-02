# Step 4 — LLM provider adapters + streaming
Built (llm/): types (Message/LLMRequest/StreamEvent/Usage/LLMError+ErrorKind with `retryable`), sse (SSE parser,
HTTP-status -> ErrorKind mapping, transport error mapping), base (LLMProvider protocol, collect()),
openai_llm / anthropic_llm / gemini_llm (async httpx SSE streaming, no SDK deps), factory.build_providers(settings)
(only providers with a key), scripts/llm_chat.py. Settings: OPENAI_MODEL / ANTHROPIC_MODEL / GEMINI_MODEL.
Design: async iterators (cancel the task / aclose() to abort mid-stream); keys only in headers (Gemini too, never URL);
errors carry status + short body excerpt, never keys; retryable kinds (rate_limit, timeout, server, network) are what
the Step 5 router will fail over on.
Tested: 57 mock tests (request shape, streaming, usage, errors x3 providers, mid-stream error, garbage stream,
cancellation, factory) + live Gemini streaming test (opt-in: RUN_LIVE=1) PASSED with real key.
Live finding: old default gemini-2.5-flash returned 404 for new users -> default is now alias gemini-flash-latest.
Known gaps: OpenAI and Anthropic only verified against mocks (no keys here); Gemini live call took ~16 s for a tiny
prompt (thinking model) — the router/agents step should pick low-latency models / cap thinking budget;
no tool-calling or non-text inputs yet; default OpenAI model name unverified.
