# Conversational answer prompt

Shared rules live in `agents/answer_style.py`; `agents/specialists.py` composes them with
the specialist, candidate context and custom session instructions. There is one authoritative
spoken-style rule and a short final reminder. Conflicting one-sentence and bullet-only
specialist instructions have been removed. The built-in toggle still omits this policy when
Off, and format/length/tone preferences remain exclusive to that mode.

The built-in policy requests the main answer first, supporting details in separate closed
text blocks, and 3 to 4 meaningful conversational markers per spoken paragraph. It explains
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
