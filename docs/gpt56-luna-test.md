# GPT-5.6 Luna live smoke test

Tested October 25, 2026 with the existing OpenAI key, the app's specialist prompts, synthetic questions, and explicit `reasoning_effort: none`. Only the opt-in benchmark script changed; no app model defaults, personal data, capture, or settings were changed.

| Question | First text | Finished |
| --- | --- | --- |
| Python list versus tuple | 1.066 s | 1.745 s |
| Second highest distinct salary SQL | 2.090 s | 4.332 s |
| Project team size, no supplied profile | 0.851 s | 1.020 s |

All requests finished with stop and closed fenced blocks. The estimated standard uncached total was $0.0009882. Pricing and reasoning support: https://developers.openai.com/api/docs/models/gpt-5.6-luna . Raw synthetic results: `tmp/gpt56-luna-live.json`. Rerun explicitly (billed) using `python scripts/test_gpt54_mini_live.py --model gpt-5.6-luna`.

The list/tuple answer was correct at the basic conceptual level, but used the prohibited prose word “or”. The team-size answer used a placeholder, avoiding an invented count; it is not a complete personal answer without supplied facts.

The SQL filtered NULL salaries and used DENSE_RANK, but omitted DISTINCT in the final projection. Executed against an in-memory SQLite fixture with salaries 100000, 90000, 90000, 80000, NULL, it returned two rows of 90000. Its illustrative output showed one row instead. Thus the displayed result and executable query disagree. This is a quality failure, not an integration failure.

Earlier single-sample list/tuple timings were GPT-4o mini 0.725 s first text / 1.810 s finished and GPT-5.4 mini 1.949 s / 2.422 s. These were separate runs, not a repeated or randomized benchmark; network, connection reuse, and server conditions can affect the comparison. Timings exclude audio capture, transcription, and UI rendering. Luna is a plausible option for simple questions, but this test does not establish it as the fastest or best model, especially for SQL correctness. Ruff passed for the benchmark script.
