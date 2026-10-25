# Faster answer startup and mock-interview answer style

Implemented in the source preview after the latency and prompt feedback.

## Latency

- Desktop composition now defaults to local question classification, avoiding the extra
  classifier API request and its timeout before answer generation. Set
  `JEV_CLASSIFY_WITH_LLM=true` to opt back into model classification. Follow-up questions
  retain their previous specialist even with the local classifier.
- Speech-end silence changes from 600 to 420 ms; the question-merge quiet window changes
  from 500 to 250 ms. Transcription time still counts toward that merge window.
- Question polling changes from 100 to 50 ms and streamed UI refresh from 50 to 25 ms.
- Saved matches remain local. Answer streaming does not wait for the completed response.

These remove avoidable waits; they do not make microphone/cloud requests instantaneous.
OpenAI STT still uploads finalized utterances in this implementation. Network time, provider
queueing and model generation remain. Shorter silence windows can split long mid-question
pauses; synthetic tests cover a 300 ms pause, but real microphone/interviewer timing needs
manual validation. No API keys, capture preferences, routing models or stored user settings
were changed. Manual/Auto Generate behavior remains the existing explicit session choice.

## Prompt contract

The format lock leads the system prompt and a final reminder includes silent Check 0.
Answer-only paragraph blocks are the selected default, resolving the conflicting requested
rules in favor of the later instruction not to repeat the question. The question still has
its own UI label. Screenshot identification retains an internal Question: metadata header,
which the app extracts into that label.

Answers lead with the requested fact, use short natural explanations, avoid unnecessary
stories, contractions, canned transitions and jargon, preserve technical abbreviations,
and limit lists to three items unless more are explicitly requested. Coding tasks use the
requested four parts: approach, code, output table and line-by-line explanation. Line N
comments must use legal syntax; sample output is labelled, never falsely claimed as executed.
The no would/while/or prose rule does not rewrite valid code keywords or literal data.

Personal claims must come from supplied facts; project-tool checks require evidence in the
same project. Skills-only tools do not establish project experience. Missing evidence is not
treated as proof of no experience. Project decisions/trade-offs must not be invented.
The fixed six-person-team and Copilot/model claims were not added as defaults because the
user has not yet confirmed they are true. Existing saved fictional examples remain unchanged
and are still distinctly labelled when retrieved.

Formatting is repaired locally after model streaming, closing unfinished fences and wrapping
unfenced paragraphs without changing wording. No additional model self-review request is
made. Typing `fix format` in Chat repairs the displayed answer locally. Prompt-level language
and factual checks guide generation but are not a guarantee that every provider complies.
Saved answers are returned verbatim, not rewritten to match the new AI style.

## Validation

- Initial affected-suite run: 234 passed.
- Expanded formatting/classifier/audio/composition run: 153 passed.
- App/settings/privacy/end-to-end/formatting run: 229 passed; the Credential Manager test
  passed separately outside the sandbox after CredWrite was blocked in the sandbox.
- Ruff passed; mypy passed across 92 source files.
- Native Windows preview confirmed saved matching and local format repair with fictional
  test data and no provider requests. Synthetic VAD test verifies a 300 ms pause is retained
  and the completed utterance closes at 420 ms of silence.

Restart Chandoo - Glass Preview to use these source changes. No installer was rebuilt.

## Cancellation hardening (October 25, 2026)

Stop now invalidates all previously submitted answer requests, including queued work,
saved-Q&A lookup, classification, and streams from either audio source. The Stop button
is enabled throughout the request and shows stopped feedback even before answer streaming.
Requests submitted after Stop remain valid. Late provider output is discarded. Partial
streaming answers retain stopped status in history; cancelled lookups add no answer record.

The local embedding worker cannot be forcibly interrupted safely: it may finish computing
in the background, but its cancelled result cannot display an answer or start AI generation.
Stopping an API stream cannot undo provider work or billing that has already occurred.

Validation: six new regression cases cover matching and nonmatching delayed lookups,
classification, repeated Stop, queued requests, concurrent audio answers, and late provider
output. Native Windows cancellation and normal-answer checks: 9 passed. Full offline suite:
778 passed, 2 skipped, 1 credential-manager test deselected; the pre-existing session-style
precedence test still fails and is outside this cancellation change. Ruff and formatting
checks passed; mypy passed across 94 source files. No paid API requests or personal data
were used. Restart the source preview to load this fix; the installer was not rebuilt.

## Answer-delay fixes after live previews (October 25, 2026)

Queued preview decoding is now discarded as soon as its final audio segment is submitted;
an already-running decode may finish, but its obsolete text remains suppressed. Each preview
model uses two CPU threads instead of four, limiting competition with the UI and answer
processing during dual-source capture. The final speech model and answer routes are unchanged.

When STT backlog drops an old segment, it emits an empty final event. This retires the matching
count of pending speech in the aggregator instead of leaving a phantom in-flight segment
that holds subsequent question text until the eight-second maximum-hold timeout. The
deterministic regression releases the remaining text at 0.3 seconds, after the ordinary quiet
interval, with zero pending speech. This is a backlog stress case, not a claim that every
ordinary response became eight seconds faster.

Answer latency now starts at submission and includes runner scheduling, saved-answer lookup
and classification. Previously the displayed AI timing excluded those stages.

Validation: 158 targeted tests and 7 native Windows checks passed. Full suite: 795 passed,
2 skipped, 1 credential-manager test deselected; the existing unrelated session-style
precedence test still fails. Ruff and mypy passed. Real cached tiny.en recognition with two
CPU threads still produced nine previews on the synthetic recording, first at 2.12 seconds.
The earlier four-thread experiment gave nine previews, first at 2.02 seconds; these single
runs verify retained previews, not a statistically established speedup.

Live API checks used only generated speech and synthetic questions, with no resume data:
cloud final transcription took 1.348 seconds; list-versus-tuple answering through GPT-4o mini
took 1.112 seconds to first text and 2.935 seconds to finish; SQL optimisation through GPT-4o
took 0.941 seconds to first text and 3.455 seconds to finish. These are separate stage timings,
not measured microphone-to-screen totals or a before/after provider comparison. Network and
provider processing still contribute latency. Restart the source preview; installer unchanged.

## Truncated answers and explicit continuation (October 25, 2026)

OpenAI `length`, Anthropic `max_tokens` and Gemini `MAX_TOKENS` are now treated as incomplete
answers, even when a later usage-only event follows the finish reason. The overlay shows
**Incomplete — output limit reached** and a **Continue answer** button. History persists the
`incomplete` status instead of `complete`; truncated answers are not added to Jev's conversation
memory. This is a new text status in the existing SQLite column, requiring no schema migration.
Unsupported finish reasons (including content filtering) report an error rather than offering
an automatic way around the provider's stop condition.

Continue is explicit and makes one new billed request. It uses the original question,
specialist, screenshot if present, and the raw partial answer, followed by an instruction to
provide only missing text. Privacy redaction and budget checks also apply to the continuation.
Display formatting may close an unfinished code fence, but those synthetic closing characters
are never inserted into the continuation context. Incoming text is appended to the original;
an exact repeated opening or substantial trailing line is removed across streaming chunks.
This is literal repetition handling, not a guarantee against every semantic paraphrase.

Provider failover resets only the new continuation text. A second token cutoff stays incomplete
and permits another explicit click. Empty/no-progress continuations report a retryable failure.
Network failure retains received text and offers another attempt; Stop, a new interview,
presentation, a newer displayed question or a stale/double click cannot continue old work.
Completed continuations enter conversation memory once, as the combined answer. Persistent
history keeps the incomplete attempt and the combined result as separate records with each
request's own cost; earlier partial attempts remain identifiable as incomplete.

There is no extra request or token increase on the normal answer path. Continue is available
for the current live answer only, not as a resume action on historical cards. Requests that
return a provider-specific blocked finish reason are not offered continuation.

Validation: 26 deterministic continuation cases passed offscreen and with native Windows
rendering, including real adapter parsing against mocked SSE responses, prefix preservation,
code blocks, redaction, screenshots, cost/budget handling, failover, repeated cutoff, retries,
double clicks and cancellation. No paid AI requests or personal resume data were used.
Ruff and mypy passed (97 source files). Restart Chandoo – Glass Preview; installers unchanged.
Full regression: 857 passed, 2 skipped, 1 real-credential test deselected. The previously known
`test_session_instructions_outrank_the_default_style_rules` assertion still fails; it is unrelated
to truncation or continuation and was not changed here.

Provider completion semantics were checked against the official OpenAI reference:
https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create

## Complete coding answers (October 25, 2026)

Coding and Data/SQL specialists now end with task-specific delivery instructions instead of
the generic spoken-paragraph example. Code-writing, debugging and query tasks request Approach,
executable code, expected Output, and a complete Line-by-line explanation. Concept questions
and explanatory follow-ups remain direct answers. Comments must explain each numbered line,
not merely label it; language syntax and indentation take priority. The prompt requests an
efficient, complete solution to the actual requirements, relevant edge cases and assumptions,
without unnecessary frameworks. All numbered lines must also appear in the separate explanation.

Completeness and correctness take priority over filler counts, terse preferences and the
three-item list limit for these tasks. Coding and Data/SQL output ceilings increased from 2,000
to 4,000 tokens to allow comments and explanations to finish. This is a ceiling, not a required
answer length. Streaming remains enabled and no validation/rewrite API request was added to the
app. Longer answers naturally take longer to finish and can cost more. Built-in prompt Off still
omits these instructions. Saved fictional answers remain unchanged. Source preview restart is
required; the installer was not rebuilt.

Validation: 148 targeted automated checks passed (answer format, built-in toggle, Jev and
continuation), with Ruff and mypy passing across 100 source files. The opt-in billed script
`scripts/test_coding_style_live.py --model gpt-5.4` sent two synthetic questions with the Short
preference, no resume or session data. Both responses completed normally with all four parts,
meaningful comments on every nonblank code line and matching line coverage in the explanation.
After manual review, their generated solutions were executed locally: Python interval merging
passed seven cases including nested/touching intervals and input-mutation checks; SQLite ranking
retained ties, excluded NULL salaries and passed sample, empty-table and NULL-only cases.

These single live samples began streaming at 2.07 and 1.66 seconds and finished at 12.58 and
13.95 seconds respectively. They are not a latency benchmark or a guarantee of model compliance
for every question. Synthetic responses are in `tmp/coding-style-gpt-5.4.json`; local execution
checks are in `tmp/check_generated_coding.py`. The app never executes generated code itself.
