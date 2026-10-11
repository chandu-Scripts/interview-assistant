# Step 17 — Session setup and model preference

Branch: `feature/parakeet-ui-redesign`. Updated October 11, 2026.

## Built

- Refined the two-page session wizard with progress/context text, a required-company
  indicator, green primary action, white form surfaces and scrolling at small sizes.
  Navigation remains outside the scrolling area. Existing resume attachment is retained.
- Answer style selectors display `Specialist default` instead of a blank option while
  retaining the empty stored value. Answer language is explicitly distinguished from
  the current English speech-transcription support.
- Back preserves the draft. Create and Cancel reset it, including resume and preferences,
  so a new interview does not silently inherit the previous company's details.
- Connect reviews model, language, answering mode and transcript saving. Recording still
  requires the existing explicit Connect/consent flow.
- The chosen model now becomes the preferred answer route, including screenshot answers
  and specialist corrections through the same Jev pipeline. Classification retains its
  normal routing. Auto uses the configured tier chains.
- Preference is per request, with the normal tier fallback chain retained and duplicate
  preferred attempts excluded. Provider health can demote a failing preferred provider.
  Attempt accounting and stream resets still run through the existing router.
- Choices come from configured routes with available provider adapters, including custom
  model names and relay fast/smart routes. Removed/unavailable choices fail explicitly
  when answering; they are not silently interpreted as Auto.
- Session activation applies the preference; ending clears it. Invalid route syntax is
  rejected before ending the current session. Role title now contributes to answer context.
- Starting an existing ready session through the dashboard reuses its record, rather than
  creating a duplicate. Ended sessions cannot be resumed through that action.

## Validation

- New end-to-end test: wizard -> return to dashboard -> resume same record -> Connect ->
  synthetic speech -> manual Answer on selected model -> End -> matching saved transcript.
  A subsequent answer verifies that ending restored normal routing.
- Additional tests cover preferred-model fallback/reset/accounting, a model from another
  tier when the default tier has no available provider, unavailable model errors, model
  preference lifecycle, clean drafts, and custom/relay choices in the assembled app.
- Inspected real Qt previews at 1280x720 and 900x560 via `scripts/preview_dashboard.py`.
- Full desktop suite: **614 passed, 2 skipped** with `QT_QPA_PLATFORM=offscreen`.
  Credential-store tests ran outside the sandbox after Windows rejected the sandboxed
  credential write. Ruff, mypy and formatting checks pass.

## Remaining gaps

- Styling follows the public Parakeet reference; exact signed-in Windows screens remain
  unverified. Live provider credentials, real audio hardware and native DPI behavior were
  not exercised by the mocked/offscreen tests.
- Post-call notes/Ask AI was subsequently added in `step-18.md`. The previously identified
  persistence/privacy and concurrent-answer lifecycle issues remain separate work.
- No commit, merge, push or deployment was performed.

## Resume management follow-up — October 11, 2026

- Added a Resumes sidebar page with a saved-resume list, plain-text preview, import,
  rename and confirmed deletion. PDF/DOCX/TXT/MD use the existing extractor and its
  12,000-character limit. The page explains that original files and existing session
  snapshots are not deleted when a library entry is removed.
- New-page extraction runs on a QThread, with visible progress and disabled mutation
  controls until completion. Shutdown waits for active extraction to finish.
- Imports on the management page refuse duplicate names. The existing wizard attachment
  flow now asks before replacing a saved entry; declining preserves the original text.
- Library changes refresh the wizard picker. A renamed/deleted selection falls back to
  the global profile with an explicit message requesting a new selection. New sessions
  use the current library contents; previously created sessions retain their snapshots.
- Added rename/delete operations, schema validation, and atomic temporary-file replacement.
  Invalid/unreadable libraries show an error and refuse mutation, preserving the original
  file. Failed writes clean up only the generated temporary file.
- Added nine tests covering extraction responsiveness, preview, duplicate and parse errors,
  rename conflicts, deletion cancellation, wizard updates, session snapshot preservation,
  corrupt-file preservation, atomic-write failure and replacement confirmation.
- Reviewed 1280x720 and 900x560 previews from `scripts/preview_resumes.py`, which uses a
  temporary library with fictional content. Full suite: **623 passed, 2 skipped** using
  offscreen Qt; ruff, mypy and formatting checks pass.
- Remaining: editing resume text, OCR for scanned PDFs, and the previously noted broader
  privacy/persistence work are outside this follow-up. The pre-existing wizard attachment
  extractor remains synchronous; background extraction applies to the new Resumes page.
