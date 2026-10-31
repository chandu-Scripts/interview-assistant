# Keep error messages visible after a queued answer refresh

The first regression run after the approved bug merge found a budget-warning test
failure. Spending was correctly blocked, but the overlay lost the warning. The
original cost test passed in isolation, so its timeout was not changed.

A pending 25 ms text-render timer from an answer could run after `on_failed` appended
an error, replacing the document with answer text that did not include the error.
This affected both partial-answer failures and a quickly submitted next question.

Codex and the installed Claude CLI independently identified this ordering issue.
Two deterministic UI tests reproduced the disappearing warning before the fix.
The overlay now stops pending text renders when starting a new question and before
displaying a failure for the current question. Stale failures remain ignored; new
answer deltas still schedule their normal refresh. Accounting and request behavior
are unchanged.

Verification: 203 app/cost/continuation/stream tests passed; five focused regressions
also passed using native Windows Qt. Ruff, overlay mypy and whitespace checks passed.
The final full merged-suite result is recorded in `docs/work-report-2026-11-01.md`.
Tests used synthetic providers and isolated data without real credentials or audio.

The change is limited to two timer stops and behavioral tests. It can be reverted
without a data migration or service change. No installer or hosted service was
updated during this merge.
