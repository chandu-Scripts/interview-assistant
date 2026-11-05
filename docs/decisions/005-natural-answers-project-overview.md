# Natural answers and project overviews

Date: 2026-11-06. Scope authorized after the six-model comparison, before merge.

## Evidence and decision

The previous 36 synthetic live answers showed forced connectors in line explanations,
unsupported incident outcomes on some models, and project overviews classified as concepts.
Codex and Claude prepared independent proposals, then reviewed the differences. Claude's
suggested HR route and higher cap were rejected: they broaden unrelated HR answers, and
live overview samples already completed within 500 tokens. Reuse the existing 700-token
behavioral specialist with explicit overview-versus-incident instructions instead.

Remove filler quotas everywhere, including coding sentences and final reminders. Keep main
answer first, useful separate paragraphs, relevant scope, factuality and code explanations.
Never add a missing metric caveat merely to complete an explanation. A direct question about
an unknown personal fact must still express uncertainty. Counts in the opt-in evaluation
script are descriptive, not a naturalness pass/fail score.

## Acceptance and limits

- Local bounded overview paraphrases must route without an LLM classification round trip.
- Counts, architecture, code, concepts and other narrow project questions keep their routes.
- One answer request, existing 700-token cap, selected Fast route preserved in split mode.
- Built-in Off omits these prompts; profile and custom instructions remain present.
- Coding retains runnable code, inline numbered comments, matching output and line explanations.
- Saved fictional answers and model defaults remain untouched; no paid rewrite introduced.

The local matcher is intentionally conservative, not universal semantic intent detection.
Unmatched paraphrases continue through the existing classifier. Legacy Auto overview requests
now use the behavioral Smart tier; explicit split Fast selections remain Fast.

Prompt instructions do not guarantee naturalness or factual accuracy. Live evaluation uses
synthetic context, not a private resume, and excludes STT/UI latency. No native layout changed.

## Validation results

- 231 automated tests passed: overview routing positives/negatives, Jev, built-in On/Off,
  prompt composition, style diagnostics, split model selection and continuation.
- Ruff passed; mypy passed on the three changed production modules.
- Same six questions and synthetic profile as the earlier comparison, on Terra and Astra:
  12/12 normal completions. Both project answers cover the business problem, data flow,
  ownership and supplied refund incident within the existing 700-token behavioral cap.
- Missing catalog counts stayed uncertain. The unrequested exact-timing caveat disappeared
  from both Spark explanations. Code explanations no longer contain forced mainly/also cycles.
- Both SQL samples executed in isolated in-memory SQLite and matched displayed expected
  output, including Terra's NULL order-key example. All code lines had numbered comments
  and corresponding explanations.
- First-text medians: Terra 1.119s; Astra 1.939s. Coding first text: 1.345s and 8.324s.
  These are single-sample checks, not a speed benchmark or proof of universal improvement.
- Astra still supplied an inferred implementation detail (broadcast hint) not explicitly
  in the profile and gave extra SQL steps. Naturalness and exact scope remain model-dependent.
- Existing separate classifier limitation observed: an imperative starting with Find and
  without a question mark can be rejected by question detection. It predates this change
  and is outside the bounded overview fix; no claim is made that all routing bugs are solved.

Rollback: revert this change commit, retaining the earlier selectable-model commit. No schema,
stored data or credential migration is involved. Merge and push need separate user approval.
