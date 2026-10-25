# Conversational answer prompt

Shared rules live in `agents/answer_style.py`; `agents/specialists.py` composes them with
the specialist, candidate context and custom session instructions. There is one authoritative
spoken-style rule and a short final reminder. Conflicting one-sentence and bullet-only
specialist instructions have been removed. The built-in toggle still omits this policy when
Off, and format/length/tone preferences remain exclusive to that mode.

The built-in policy requests the main answer first and supporting details in separate closed
text blocks. Short factual answers have no minimum filler count, usually zero to two natural
markers. Longer explanations request 3 to 4 meaningful markers per spoken paragraph. It explains
marker purposes and prohibits empty filler padding. Code, comments and output tables are
exempt. No em/en dashes in prose is now a built-in rule, listed in Features. The policy
preserves supplied facts and does not invent missing personal counts or project experience.

Coding tasks request four parts: approach, executable code, expected output and complete
line explanations. Each nonblank code line needs a meaningful numbered inline comment
where language syntax permits it. Separate explanations cover every numbered line.
Type annotations must match actual types; SQL should handle nullable grouping/join keys.
Concept questions do not require manufactured code. The coding output budget stays 4,000.

No automatic filler insertion or additional model rewrite pass was added. Streaming and
saved fictional Q&A are unchanged. Exact counts, naturalness and code correctness remain
model-dependent; these prompt instructions are not an output validator.

## Validation on October 25, 2026

215 focused automated tests passed, covering prompt composition, built-in On/Off, custom
instructions, actual mocked provider requests, rendering, continuation, saved-Q&A matching
and end-to-end flows. Ruff and mypy passed. A continuation retry test now waits for Qt's
completion signal before reading rendered text, removing a test race exposed during this run.

Four live evaluation rounds sent 30 synthetic questions through the real Jev/router path
using GPT-5.4-mini for short answers and GPT-5.4 for coding. No real resume, audio or session
data was sent. The first three rounds guided revisions; they are not results for the final
prompt. Run the opt-in billed evaluation with:

```powershell
.\.venv\Scripts\python.exe scripts/evaluate_answer_style.py --live --output tmp/answer-style-eval.json
```

`--repeats 2` repeats the suite; `--cases python sql_code` limits the cases. The evaluator
records full synthetic answers, timing and structural checks. It never executes generated
code. Its literal marker counter is diagnostic and cannot judge naturalness. Code/output
blocks and inline code are excluded from marker counting. Review the full answer as well.

The final six-case run (`tmp/style-eval-final.json`, ignored by Git) found:

- All six answers finished normally, stayed fenced and avoided prohibited dash punctuation.
  The shopping analogy and Python type-hint instructions were followed.
- Both coding answers included four parts, numbered inline comments and every line explained.
  After manual code review, Python passed seven runtime/nonmutation cases and mypy. SQLite
  passed the supplied ties example, empty table, NULL-only salary and NULL department checks.
- Three answers met the literal filler-count rule in every checked paragraph. SQL had one
  six-marker paragraph, and team-size/missing-fact answers had only two markers. Some wording
  still sounded scripted or assistant-like, and SQL prose included "would". This is partial
  style improvement, not full acceptance of the human-answer or exact-count requirement.

Earlier rounds also exposed an incorrect generated Python type annotation and SQL equality
joins that dropped NULL departments. Final samples passed those checks after added guidance,
but this does not guarantee future generated code. Saved answers remain the reliable path
for exact user-authored wording. Older GPT-4o-mini evaluations also failed exact marker counts;
switching models alone has not established consistent compliance.

Restart the source preview to load this policy. The installed release and website installer
were not rebuilt. No settings, model selection or personal saved answers were changed.

## Short-answer investigation before the approved exception

`fix/direct-answer-wording` removes the three-sentence quota and quoted examples of unwanted
wording. It adds concise illustrative answers, keeps real candidate context after the
examples, and asks for a direct answer without source commentary or a concluding sentence.
The 3 to 4 marker requirement is still present. There is no runtime phrase filter, new model,
buffering step or second model request. Examples are prompt guidance, not hardcoded question
matches or candidate experience. Saved Q&A remains untouched.

The live evaluator now includes tuple modification, supplied pipeline counts, documented
project use and skills-only knowledge. Commentary flags are review aids, not a semantic
grader or production output filter. Read the full answers; zero flags does not mean natural
wording or grounded experience.

Four sets of 12 synthetic short answers compared the original policy and three revisions
(`tmp/direct-style-before.json`, `direct-style-after.json`,
`direct-style-examples-retry.json`, `direct-style-final.json`). One additional attempted run
timed out before its first answer and was retried without changing production timeouts.
The last revision preserved supplied counts, avoided the earlier unsupported Kafka project
claim in these samples, and generated correct tuple explanations. It still produced source
commentary and some redundant closing clauses. Only 6/12 answers met the exact marker rule,
versus 9/12 for the baseline; improved style consistency is not established.

Median first-text latency was 0.94 seconds for the last run versus 0.99 seconds for baseline.
Median total duration was 1.31 versus 1.25 seconds. These small sequential samples do not
establish a speed improvement, and exclude the failed attempt. No architectural latency
was added. 220 regression tests passed, plus two new streaming tests that prove the first
chunk is delivered before provider completion with one request in both built-in modes.
The specialist-correction test now waits for its Qt signal before checking UI delivery.

These revisions did not establish full acceptance. The user was asked about allowing fewer
fillers in very short answers to avoid padding, and approved that change.

## Approved short-answer rule, October 26, 2026

The prompt and final reminder now explicitly exempt short factual answers from a minimum
filler count. Count answers state the number and breakdown once; brief uncertainty can be
one sentence. Longer explanations, coding approach and line explanations retain the 3 to 4
marker guidance. Features shows the same distinction. Examples of short answers were changed
to demonstrate this rule without introducing new candidate facts. The runtime still makes
one streaming request and does not insert/delete words to meet a quota.

The evaluator's short-answer cases are explicitly annotated, not classified after observing
the output. Only a single paragraph in an annotated short case receives the relaxed count
check. Counts and commentary flags remain diagnostics; human review is required.

223 focused tests passed, including first-chunk streaming, built-in On/Off, prompt composition,
provider requests, formatting, saved-Q&A matching, continuation and end-to-end behavior.
Ruff passed. Windows initially blocked mypy's compiled DLL in the sandbox; the same check
outside the sandbox passed all 101 source files without changing security settings. Two
pre-existing Qt test races (specialist correction and continuation failover) now await their
completion signals instead of checking worker state before queued UI events arrive.

Live checks used synthetic data only:

- A ten-question suite (`tmp/direct-style-natural.json`) retained four-part Python/SQL
  answers, numbered code comments, full line explanation coverage and custom instructions.
  No claim of executing these generated code samples is made in this pass.
- A repeated six-question run (`tmp/direct-style-approved-final.json`) produced 12/12
  completed, fenced answers without em/en dashes; all met the relaxed short-case marker
  check. Counts stayed grounded, tuple explanations were correct, missing counts stayed
  uncertain, and skills-only answers did not invent project experience. Team and pipeline
  answers no longer padded their breakdowns with confirmation sentences.
- Remaining limits: some tool-experience responses still add redundant wording or refer to
  unspecified project experience. Exact 3 to 4 counts in longer answers are not reliable.
  The change improves the short factual cases; it does not guarantee human-sounding output.

Median first text for the final 12 answers was 0.81 seconds and median total duration was
1.03 seconds, versus 0.99/1.25 seconds in the earlier baseline. Sequential small samples do
not establish a performance guarantee. No extra model call, response buffer or artificial
wait was added. The installer was not rebuilt and the feature branch was not merged.
