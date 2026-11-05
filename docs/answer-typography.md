# Answer typography

Settings > Appearance controls answer font family (Segoe UI, Arial, Calibri,
Consolas), weight (Regular, Medium, Bold), and size (Small 10 pt, Medium 11 pt,
Large 14 pt). The default remains Segoe UI / Regular / Medium. The preview uses
the real answer renderer and synthetic prose and SQL. Missing fonts are labelled
as using a system fallback; available weights depend on Windows fonts.

Preview changes are drafts. Save persists through the existing account-aware
settings store and updates existing and future overlay answer cards immediately.
Cancel does not apply the draft. Startup restores the saved choice. Other changed
settings still follow their existing restart requirements. Code uses regular
Consolas with the chosen size. Clipboard text, source text, prompts and model
routing are unchanged. Existing incognito settings-preference semantics are retained.

## Inline answer controls (6 November 2026)

The live answer toolbar now has a Font toggle beside Clear and Copy. It reveals
Font family, Font style and Font size in the same window. Each explicit choice
is saved before applying to existing and streamed cards. A failed write restores
the previous choice and shows an inline error. Settings and the toolbar share
the account settings store; synchronization preserves unrelated Settings drafts.
Defaults remove font overrides. Inactive accounts and presentation cannot edit.
Closing the row or hiding its parent closes any open font dropdown.

Codex and Claude compared inline versus separate-popup controls; inline controls
keep the requested single-window flow. Preferences retain existing incognito
behavior. There are no provider requests or changes to generated answer content.
Validation: 46 typography/inline tests and 107 app tests passed. The first combined
run exposed an existing cancellation-history timing flake; the full app test file
passed on rerun. Ruff and targeted mypy passed. Native Windows inspection at
640 x 500 verified the themed row and an immediate Medium-to-Large change in
both prose and monospace SQL, without resizing the window. Claude's read-only
review found no blockers. The constructor loads the Settings baseline before
the inline binding, addressing the review's baseline initialization question.
Rollback is reverting this UI commit. The installer has not been rebuilt.

## Review and validation (6 November 2026)

Codex and the installed Claude CLI prepared independent proposals and compared
them before implementation. Both selected typed settings and an AnswerView-based
preview, with explicit local label styling to override inherited global theme fonts.
Claude initially excluded Consolas from prose; the user's explicit list takes
precedence, so it is included. Global application-font changes were rejected because
they would resize navigation and controls. No font downloads or schema migrations.

179 tests passed across answer typography, answer rendering, settings, provider
picker and customer-owned-key regressions. Three composition tests passed,
including startup restoration and live Save wiring without a restart prompt.
The typography matrix covers all 36 family/weight/size combinations under the
overlay stylesheet, streaming updates and unchanged copy contents. Additional
tests cover draft cancellation, persistence/reload, mixed restart requirements,
invalid settings, write failure and existing incognito preference behavior.

Native Windows inspection of the actual SettingsDialog at 840 x 660 confirmed
the Appearance controls and Medium-to-Large live preview, unchanged window size,
and monospace SQL. Native inspection does not cover every DPI/font combination;
all combinations were covered by offscreen widget tests. Ruff and targeted mypy
checks passed. No provider calls or real customer records were used for UI tests.
Claude's final read-only diff review reported no blocking findings; the test and
native verification results above were independently run by Codex.

The feature is isolated on `feature/answer-typography`. Merge and push require
separate user approval. No installer was rebuilt. Rollback is reverting the
feature commit; older builds ignore the added nonsecret settings keys.
