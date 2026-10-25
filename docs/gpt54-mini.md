# GPT-5.4 mini option and live test

Added October 25, 2026 to the source preview's Settings > AI providers > OpenAI fast/smart model lists. Existing defaults and user settings remain unchanged. A successful key check replaces suggestions with account-visible models. Save a selection and restart to apply; explicit routing-chain/session overrides still take precedence.

The OpenAI adapter supports the alias and dated snapshots with explicit `reasoning_effort: none`, streaming, completion-token limits, and usage accounting. It omits temperature for this model. Standard pricing is $0.75/M input and $4.50/M output; the current price tracker conservatively charges cached input at the uncached rate.

Source: https://developers.openai.com/api/docs/models/gpt-5.4-mini

## Live observations

Four requests used the existing configured OpenAI key, the app's specialist prompts, and synthetic questions, with no resume, microphone, history, saved Q&A, or fallback provider. One sample per question/model; this is a smoke test, not a statistically meaningful benchmark. Timings start at the model request and exclude speech capture/transcription and UI rendering.

| Model / question | First answer text | Complete response |
| --- | --- | --- |
| GPT-5.4 mini / list versus tuple | 1.949 s | 2.422 s |
| GPT-5.4 mini / second highest distinct salary SQL | 1.206 s | 2.509 s |
| GPT-5.4 mini / unknown project team size | 1.181 s | 1.600 s |
| GPT-4o mini / list versus tuple | 0.725 s | 1.810 s |

All four finished normally. Estimated total standard token cost: $0.00373485. Synthetic response details are in `tmp/gpt54-mini-live.json`; rerun explicitly with `python scripts/test_gpt54_mini_live.py` (billed).

## Quality review and limitations

- GPT-5.4 mini correctly explained list mutability versus tuple immutability and returned closed paragraph blocks. It still used the prohibited prose word “or”.
- SQL used DENSE_RANK correctly to rank distinct salary values, but did not deduplicate the final result and did not filter NULL salaries. It can therefore return repeated rows and has dialect-dependent NULL ordering. The output was a placeholder rather than a concrete illustrative table. Do not treat this as a fully validated coding answer.
- The team-size answer avoided inventing a count, but added an unnecessary offer to help instead of staying concise.
- GPT-4o mini was faster on the one shared question; it also missed prose constraints. These observations do not establish either model as universally faster or better.

The integration tests cover request parameters, streaming/usage, settings selection/persistence, and pricing. Existing provider/router/config/cost tests passed (one existing opt-in live test skipped); the credential test required a separate unsandboxed run. Ruff and mypy passed. No installed release or website download was rebuilt.
