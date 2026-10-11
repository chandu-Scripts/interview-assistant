# Step 18 — Session dashboard, Parakeet UI refresh

Branch: `feature/parakeet-ui-redesign`. Reviewed and implemented October 11, 2026.

## Built

- Replaced the dark startup list with a light desktop dashboard: workspace sidebar,
  green Create Session action, session table, All/Active/Ended filters, search, status
  badges, and actions on each row. The main window uses standard Windows window controls.
- Search matches company, role and job description, together with the selected status
  filter. Refresh preserves the selected session when it is still visible.
- Start Session uses the clicked row and opens the existing Connect page. Capture still
  starts only after the user clicks Connect and passes the existing recording consent gate.
- View Transcript opens the newest saved transcript associated with that interview
  session. If none exists, history shows an explicit empty state instead of another call.
- Sidebar actions open history, settings and privacy. The existing two-page creation
  form inherits the light theme, with a heading and bounded text-editor heights.
- Added `scripts/preview_dashboard.py`: renders the real Qt widgets with fictional,
  in-memory sessions to `tmp/dashboard-preview/`, without audio, keys or user data.

## Validation

- Regression coverage includes combined search/status filters, empty results, stable
  selection, row-specific actions, the explicit Connect boundary, and transcript selection.
- Visually inspected dashboard and creation-form previews. Fixed stylesheet specificity,
  clipping in table actions/status badges, and explicit fonts for offscreen Windows previews.
- Corrected existing mypy issues in the resume collection annotation and device-menu
  callback; lint and mypy now pass.
- The full suite exposed excessive work in email redaction on long non-email strings.
  Added a token-start boundary to avoid repeatedly scanning overlapping suffixes;
  the 62 privacy/security tests pass, including the existing performance regression test.
- Final standard check: ruff and mypy pass; pytest reports 603 passed, 2 skipped and
  one failure caused by Windows `OpenClipboard` contention, not an answer-selection
  assertion mismatch. The complete overlay/controller module then passes (82 tests)
  with `QT_QPA_PLATFORM=offscreen`, including that failed copy/navigation scenario.
  All 604 runnable test cases have therefore passed across these runs; the native
  desktop full-suite run itself was not entirely green. Formatting checks pass for
  all nine changed/new Python files.

## Scope and remaining gaps

This is the first dashboard pass, based on the public Parakeet product reference at
https://www.parakeet-ai.com/. It is not a verified pixel-exact reproduction of the signed-in
Windows app. Public reference screenshots are under `tmp/parakeet-ui-review/`.

- The live overlay visual pass is recorded in `step-15.md`; resume management is in
  `step-17.md`. Post-call review is recorded below. Existing app
  branding and explicit privacy behavior remain.
- Active means not ended; the database does not distinguish ready from live here. No live
  status is fabricated. Duration uses the existing creation-to-end timestamps.
- The dashboard uses the store's existing 200-session list limit; pagination is not added.
- The session-model preference and duplicate-ready-session behavior were subsequently
  addressed in `step-17.md`. Privacy persistence and concurrent-answer lifecycle gaps
  identified in the project review remain separate work.
- No merge or deployment was performed.

## Post-call review follow-up — October 11, 2026

- Ended dashboard rows now offer View Session, opening Transcript, Notes, Ask AI and
  Call details inside the workspace. The older History window remains available for
  search, export and deletion. End retains its existing close-app behavior; review
  is accessed from the dashboard on a subsequent launch.
- Transcript explicitly means saved questions and AI answers, not a word-for-word
  recording of the conversation. All turn groups linked to the selected interview
  are loaded chronologically, without the dashboard's 200-item limit.
- The isolation regression exposed a pre-existing history boundary bug: switching the
  active interview did not close its open turn group. A changed interview ID now closes
  that group so subsequent turns use the new interview link; existing turns are untouched.
- Notes use SQLite schema 5 with migration from schema 4 and earlier. Saving is explicit
  and respects history-off/incognito. Drafts survive page changes in memory; closing
  the app discards unsaved drafts. Clearing and saving removes the note text. Delete All
  and retention remove stored notes as well.
- Ask AI sends only the selected session's saved Q&A and the current question, with
  outbound PII/secret redaction. It runs on the existing asyncio thread, separately from
  live Jev conversation state, and uses configured routing/fallback, the session's model
  preference, cost accounting (tag `post_call`) and hard-budget checks.
- Review requests are explicit and independent. Responses stream on screen and are not
  saved. Stop, navigation and presentation mode cancel the request; generation checks
  reject stale output. Before asking, Q&A is re-read to avoid sending already-deleted data.
- Context is capped at the first 24,000 characters with an on-screen notice when truncated.
  Empty sessions disable Ask AI. The prompt requests question-number citations and requires
  missing evidence to be acknowledged; generated analysis still needs user review.
- Details show company, role, timestamps, saved question count, recorded answer cost,
  model preference, answer language and job description. Recorded answer cost does not
  claim to include speech costs or post-call review requests.
- New tests cover migration/data preservation, note privacy/deletion/retention, multiple
  transcript groups beyond list limits, drafts, redaction/context bounds, billing, fallback
  reset, cancellation, deleted data, budgets, presentation and dashboard navigation.
- Inspected previews at 1280x720 and 900x560 using `scripts/preview_post_call.py` with
  fictional in-memory data and no provider calls. Final full suite: **631 passed, 2 skipped**
  with offscreen Qt. Ruff, mypy (86 source files), formatting and diff checks pass.
- Remaining: full conversation transcription storage, generated/persisted summaries,
  review chat history, native Windows/provider validation, and the broader previously
  identified privacy/concurrent-answer issues. No commit, merge or deployment performed.
