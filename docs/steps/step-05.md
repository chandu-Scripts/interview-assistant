# Step 5 — Model router + fallback chain
Built: llm/router.py (Router, Route, RouteEvent, AttemptRecord, AllRoutesFailed, NoRoutesAvailable, collect_routed),
llm/router_factory.py (parse_chain, default_chains, build_router), scripts/router_demo.py.
- Tiers: "fast" (openai gpt-4o-mini -> gemini-3.1-flash-lite -> anthropic haiku 4.5) and "smart" (anthropic sonnet-5 ->
  openai gpt-4o -> gemini-flash-latest). Only providers with keys are used. Override: ROUTER_FAST / ROUTER_SMART
  ("provider:model,provider:model"); provider pinning per request.
- Failover on any LLMError, no first token within ROUTER_FIRST_TOKEN_TIMEOUT_S (10s), or stall >20s mid-stream.
- Mid-stream failure -> next route restarts the answer and emits RouteEvent.reset=True (UI must clear partial text).
- Circuit breaker per provider: opens after 2 consecutive failures (auth: immediately, 10 min; rate limit 60s; else 30s);
  an open provider is demoted to last resort, never removed, so a chain is never empty.
- on_attempt(AttemptRecord) for every attempt incl. failures (route, latency, first-token time, usage, error) -> Step 8 cost tracking.
- Cancellation propagates and closes the upstream stream.
Tested: 72 tests pass (15 router tests with scripted providers + fake clock). Live: fast tier via OpenAI 1.2s first token;
smart tier via gpt-4o OK; forced failover (bogus OpenAI model -> Gemini) recovered in ~2s total.
Live finding: gemini-flash-latest alias took 5-29s to first token (10s timeout tripped in the demo); gemini-3.1-flash-lite
~1.2-1.6s -> now the fast-tier Gemini model (GEMINI_FAST_MODEL). Model names/latency should be re-checked periodically.
Known gaps: Anthropic routes untested live (no key); router picks tier only from the caller — automatic question
classification is Step 6 (Jev); no latency-adaptive reordering (only failure-based); cost-aware ordering comes with Step 8.
