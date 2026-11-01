# First-run setup readability

After the first customer sign-in, Windows selected `QWizard.AeroStyle`. Its native
white content areas were painted underneath the app's light text. The Welcome page
also opened at 519 x 390 logical pixels with part of the recording disclosure clipped;
moving to Providers increased the height to 606 pixels.

Codex reproduced this with a native Windows preview using synthetic state, memory-only
keys and a temporary data directory. Claude independently reviewed the wizard/theme
code before implementation and identified the same native-style mismatch.

The fix retains the existing four-page wizard and all completion/consent/key behavior.
It selects Classic style, applies the shared navy/cyan theme locally, and centers a
screen-bounded 840 x 660 logical-pixel window. Disabled speech choices remain legible.
Provider rows scroll when space is short instead of collapsing key fields and buttons;
Skip and Back/Next/Finish/Cancel remain outside that scroll area.

Changing the global application font or replacing the account/onboarding flow was
unnecessary. No account data, Supabase configuration, API credentials or saved settings
are changed by this appearance fix. Rollback is a revert of the feature commit.

## Validation

Native before/after previews exercise Welcome, Providers, Speech and Profile. They use
the real Windows Qt backend and process-local scale factors, without changing Windows
display settings. Synthetic customer billing text is included. The 200% preview caught
collapsed provider controls and prompted the scroll-area correction.

An initial regression run had 129 passes, one skip and a Windows resource-exhaustion
setup error (`WinError 1450`); Windows also emitted a native COM event-processing warning.
The overlapping full-import type-check was stopped to reduce memory use. Final
verification is run separately with offscreen tests and isolated native visual checks.

Final results:

- Existing configuration, key-paste, customer BYOK and account integration tests:
  **131 passed**, run with `QT_QPA_PLATFORM=offscreen`.
- Five new appearance tests plus the four existing onboarding tests: **9 passed**.
  The new tests cover rendered page backgrounds, foreground contrast, consent/skip/cancel
  gates, three work-area sizes (1280 x 900, 840 x 620, 960 x 510), reachable scrolling
  controls and wrapped provider status. Their Windows fixture explicitly loads Segoe UI;
  otherwise Qt's offscreen backend substituted missing-font squares and gave incorrect
  wrapping metrics. Tests never access real credentials, network or account data.
- Ruff lint/format and `git diff --check` passed. Mypy passed for the two changed source
  files with `--follow-imports=skip`; a full-project type check was not completed.
- Four-page native Windows previews ran at 125%, 150% and 200% effective scaling;
  captures were inspected for readability and clipping. Client sizes were 840 x 660, 840 x 616 and
  840 x 480 logical pixels. Provider fields retain their height and remain reachable
  by scrolling at 200%; Skip and navigation remain visible.
- Claude reviewed the final source/test artifacts and found no blocking regression.
  Its test-quality and unfinished-report notes were addressed before committing.

The native preview captures are kept in the ignored root `tmp/onboarding-final-125/`,
`tmp/onboarding-final-150/` and `tmp/onboarding-after-scroll-200/` directories. Hosted
email/recovery and live API-provider checks are outside this appearance fix. Work areas
below 480 logical pixels in height retain the shared sizing helper's existing limit.

The running personal app/installed release is not replaced by a feature-branch change.
Merge and push remain separate user decisions.
