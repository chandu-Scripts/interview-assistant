# Step 15 — Live overlay visual refresh

Branch: `feature/parakeet-ui-redesign`. Updated October 11, 2026.

## Built

- Three separate rounded, translucent surfaces: a compact action toolbar, a live
  transcript strip, and an answer panel. Toolbar shortcuts have individual key badges.
- Answer navigation, Clear and Copy sit above the question. Routing/cost metadata,
  Stop, opacity and the resize grip sit below the streaming answer.
- Incoming speech updates the transcript strip without replacing the question for
  the answer being read. Pending questions remain answerable after clearing the strip.
- The strip keeps at most 2,000 characters, truncates visually to its available width,
  and exposes the retained text on hover. This does not change stored transcripts.
- Collapse and bubble modes restore the new panels. An unbound microphone stays
  hidden after returning from bubble mode; collapsed height is preserved on return.
- Screenshot help now describes the existing immediate-answer behavior.
- Added `scripts/preview_overlay.py`, which renders fictional answer/chat/collapsed/
  bubble states without capturing audio/screens or accessing providers or user settings.

## Validation

- Overlay/controller regression suite passed: 84 tests, using the offscreen Qt backend.
- New regression coverage checks that incoming and pending speech preserve the answer
  under review, clearing the transcript preserves Answer availability, and bounded
  transcript display survives collapse/bubble transitions.
- Inspected real Qt renders of the answer, chat, collapsed toolbar and bubble.
- Ruff and mypy pass. Full desktop suite: **606 passed, 2 skipped** with
  `QT_QPA_PLATFORM=offscreen`. Credential-store tests ran outside the filesystem sandbox.

## Known gaps

- This uses the public reference at https://www.parakeet-ai.com/; it is not a verified
  pixel-exact reproduction of the signed-in Windows client. App branding is retained.
- Real audio/provider calls, native desktop transparency and mixed-DPI monitors still
  need a manual Windows acceptance run. Offscreen previews cannot validate those.
- Session wizard/model selection and resume management were subsequently addressed in
  `step-17.md`; post-call pages are in `step-18.md`. Previously identified
  persistence/concurrency issues remain separate work.
- Changes are uncommitted on the feature branch; no merge or deployment was performed.
