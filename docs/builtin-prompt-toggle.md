# Built-in prompt toggle

The session wizard places a sliding navy/cyan switch directly below AI instructions.
Its thumb animates over 160 ms; cyan/right means On and muted/left means Off.
Its visible On/Off label and checked state expose the choice to keyboard and accessibility
users. New forms and cancelled forms default to On.

On preserves the built-in prompt, specialist template, format reminder and spoken-style
example. Off omits all of those, including forced paragraph fences, fillers, wording bans
and the four-part coding template. The controller also skips automatic fenced-block
normalization when Off, so custom output is not reformatted after generation finishes.

Both modes keep relevant resume/job context, selected language, custom AI instructions and
basic factuality guidance. Format, length and tone are now mutually exclusive with built-in
style: On disables those three dropdowns and excludes their selections from the system prompt.
Off enables them and includes nondefault preferences. Selections remain saved for later use,
including across session resume; enforcement happens in the prompt builder so old sessions
cannot accidentally mix both modes. A visible notice and disabled-field tooltips explain how
to enable the controls. Off with no custom instructions uses
a minimal question-answer prompt. Classification, provider routing, redaction, response token
limits, fallback and cancellation are unchanged. Saved fictional Q&A remains verbatim.
Copy buttons are interface controls and remain available. An explicit Fix format command
still performs the requested formatting.

Schema 7 adds use_builtin_prompt with default 1, preserving the behavior of older sessions.
The flag round-trips through SQLite, transient/incognito sessions, ready-session resume and
session reuse; ending a session restores On. The connection summary shows the chosen state.

Tests cover all specialist prompts in Off mode, actual generation requests in both modes,
controller output normalization, form reset, persistence/resume, schema 6 migration and the
native Windows layout. API providers and audio are mocked; no paid requests are needed.

The Features button beside the toggle expands a navy/cyan panel inside the same workspace.
It shows short titles for the current built-in rules, not the full prompt. The scrollable list
is available in both modes, automatically scrolls into view and has a Close button. Cancel or
creating a session collapses it. The titles live alongside the built-in prompt in
`agents/specialists.py`; they describe requested behavior, not guaranteed model compliance.
Copy buttons are a UI feature. The no-em/en-dash rule is now built in and listed in Features.
Shared policy lives in `agents/answer_style.py`, composed by `agents/specialists.py`.
See `spoken-answer-style.md` for live model checks and remaining compliance limits.

Validation for this change: 183 focused automated tests passed, with the pre-existing unrelated
session-instruction-priority assertion excluded. The 24 built-in prompt tests also passed on
native Windows. They cover mouse/keyboard interaction, reset, saved selections, actual mocked
provider requests after session resume, all specialists, panel visibility and compact sizing.
Ruff and mypy passed. Source preview restart is required; the installer was not rebuilt.

Pre-push full regression: 994 passed, 2 skipped, 1 real-credential-store test deselected.
The old session-priority assertion now checks the agreed built-in/custom modes instead of
obsolete prompt wording. The end-to-end session test covers both modes. The v4 migration
fixture removes columns added in v6/v7 before marking itself as v4, and continuation tests
wait for Qt completion signals rather than reading worker history before UI delivery.
Ruff, mypy and download-website JavaScript syntax checks passed. This is a source checkpoint,
not a newly packaged installer or website deployment.
