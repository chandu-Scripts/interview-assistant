# Step 8 — Cost tracking
Built (cost/): pricing.py (PriceBook: longest-prefix model rates, audio per-minute rates, JSON overrides, PRICES_AS_OF),
tracker.py (CostTracker: thread-safe UsageRecord log, Summary by provider/model/tag with llm/stt split and "wasted" spend on
failed/cancelled attempts, sessions, day/month scopes, Budget + BudgetState with warn at 80% and optional hard stop, JSONL
UsageStore persisted to %APPDATA%/InterviewAssistant/usage.jsonl - metadata only, never prompts/answers), factory.py.
stt/metered.py (MeteredSTT reports audio seconds per call). Router AttemptRecords now carry tag, input/output chars and a
cancelled flag (cancelled and mid-stream-failed answers are billed too, estimated from text length when no usage arrived);
Jev tags calls with the specialist name or "classify". Controller: budget hard stop (refuses new answers with a clear message),
per-answer cost signal, cost_updated signal, new_interview() (clears context + starts a new cost session). Overlay: session cost
in header (amber/red near/over budget, tooltip breakdown by model/STT/waste), per-answer cost in the chip. Main window: session
cost label + "New interview". Billed STT (OpenAI) disables partial transcripts (they re-send the whole utterance).
Prices verified 2026-09-27 from vendor pages: gpt-4o $2.50/$10, gpt-4o-mini $0.15/$0.60 (per 1M tok), Sonnet 5 $2/$10 (intro
price made permanent), Haiku 4.5 $1/$5, Gemini 3.1 flash-lite $0.25/$1.50, gemini-flash-latest alias priced as 3.8-flash
$0.75/$3.75 (promo until 2026-12-31, doubles 2027-01-01), OpenAI transcribe $0.003/min. Unknown models show "$x+" (never $0).
Settings: BUDGET_USD, BUDGET_SCOPE, BUDGET_HARD_STOP, USAGE_LOG, DATA_DIR, PRICING_FILE.
Tested: 177 tests pass (21 new: price lookup/overrides, totals, unpriced, waste/estimates, sessions/scopes, persistence + corrupt
lines, budgets, metering, partials off, router->tracker, controller/overlay). Live E2E: gpt-4o-mini 165 in/100 out = $0.000085
(matches hand calculation); OpenAI STT 5.7s = $0.000284 (matches $0.003/min); local STT $0.
Known gaps: costs are estimates from token counts, not the provider invoice (cached-token discounts, batch/priority tiers, and
Anthropic's newer tokenizer differences are not modelled); Gemini alias pricing can drift; no history/export UI yet (Step 9
should reuse usage.jsonl or move it into SQLite); Anthropic path still untested live; budget is per-process (two app instances
share only the day/month log, read at startup).
