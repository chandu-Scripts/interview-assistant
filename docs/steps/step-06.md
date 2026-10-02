# Step 6 — Jev router + specialist agents
Built (agents/): types (Category, Decision, Turn), rules (regex scoring classifier, looks_like_question, is_follow_up),
classifier (rules first; small LLM call via router "fast" tier only when rules confidence < 0.7; 2.5s timeout; any failure ->
rules guess "fallback"), specialists (behavioral/hr/coding/system_design/data_sql/concept/general: prompt, tier, max_tokens;
candidate profile injected), jev (Jev.ask() yields a Decision then RouteEvents; short memory of last N Q/A with char budget;
interrupted answers not stored; failover resets handled). scripts/jev_demo.py. Settings: CANDIDATE_PROFILE, JEV_CLASSIFY_TIMEOUT_S.
Design: ONE specialist per question (orchestrator + specialists), no parallel agents (cost/latency).
Behaviour: filler/acknowledgements ("Okay, great.") produce no answer; short follow-ups ("What was the outcome of that?") inherit
the previous specialist without an LLM call; long (>60 words) fast-tier questions upgrade to smart.
Tested: 134 tests pass incl. a 28-question labelled corpus for rules (100%), classifier fallbacks (LLM error/garbage/timeout),
Jev flow with scripted providers (history, prompts, cap, failover, cancellation).
Live (OpenAI+Gemini): filler skipped; behavioral/coding/system-design/concept routed to the right specialist; rules decide in
0.00s, LLM classification ~1.1-1.5s for ambiguous; follow-ups 0.00s; first token 0.7-1.3s.
Known gaps: rule corpus is small and English-only (real accuracy on live interviews unknown - log decisions in Step 9);
interviewer questions split across pauses are not merged (utterance aggregation belongs in Step 7 wiring); Jev not yet wired to
STT/UI; no per-agent tools; Anthropic path untested live.
