# Isolated bug fixes and production preparation

Date: 2026-11-01. User authorized work on the audited bugs and pending features in
separate branches. Main, hosted services, user data and published installers remain
untouched. Existing login foundation is preserved at `683efd1` on
`feature/account-login`; all initial work branches descend from that commit.

## Independent review and decisions

Codex and the installed Claude CLI prepared approaches independently, followed by
a focused challenge round. Source evidence and regression tests decide correctness.

| Item | Branch | Acceptance |
|---|---|---|
| B1 Saved answer direction | feature/saved-answer-direction | Opposite entity roles cannot return the saved answer, including semantic lookup; normal paraphrases remain covered. |
| B2 Stream completion | feature/stream-completion | Missing terminal signal never becomes a complete remembered answer; partial text, accounting and explicit continuation remain correct. |
| B3 Stale audio | feature/stale-transcription | Stopped capture invalidates queued inputs, workers and queued UI results for both sources; fresh captures still work. |
| B4 History actions | feature/history-search-actions | Search hit, transcript, export and confirmed deletion refer to the same session. |
| B5 Relay budget | feature/relay-budget-reservations | Concurrent admissions reserve cost atomically; unknown prices and invalid messages fail before upstream; cancellation/crash do not erase spending. |
| B6 Credential removal | feature/credential-removal | Vault failures cannot produce a false removal notice or discard operational state. |

Preserve existing provider fallback behavior, privacy/incognito, account isolation,
local data, cancellation and source-development mode. Each branch has synthetic
regression tests, lint/type checks and an implementation note. Combined behavior
will be tested in a disposable checkout without merging feature branches into main.

Rejected approaches: embedding thresholds alone cannot detect reversed roles;
current-recording flags alone cannot distinguish an old callback after restart;
silently treating failed vault reads as absence cannot prove deletion; a budget
check before a network call without an atomic reservation cannot bound concurrency.

Rollback is branch/code reversion for UI/worker fixes. Any server schema change must
retain existing data and document version compatibility and recovery. Do not apply
schema changes to the live relay during development.

## Commercial feature boundaries

Branches created: `feature/customer-admin`, `feature/customer-entitlements`,
`feature/payments`, `feature/cloud-backup-sync`, `feature/legacy-import`,
`feature/production-release`, `feature/auth-email-acceptance`.

Build common access/admin infrastructure, explicit portable data/import operations,
and release/hosted-email verification tooling with synthetic identities and data.
Access roles and entitlements must be server-owned, never user metadata. Monitoring
is operational metadata only. Backup remains opt-in and incognito prohibits mutation.
Original personal data must not be adopted, uploaded, moved or deleted automatically.

The user has been asked whether customers bring their own provider keys or the owner
funds AI usage through the server. This is a material commercial decision. A desktop
access check cannot enforce paid provider usage against a modified BYOK client.
Do not claim such enforcement, activate checkout, invent prices or add a funded plan
without that decision and a configured provider. Provider-neutral contracts can be
tested independently; an incomplete integration must be clearly labelled.

Custom SMTP/domain, signing identity and hosted acceptance are not available yet.
No actual emails, payments, cloud uploads or releases are part of fake-provider tests.
Domain purchase was explicitly deferred by the user. Dependencies requiring these
inputs remain pending; do not work around them by disabling verification.

Before merging, report concrete branch results and conflict checks. Merge and push
retain their separate user-approval gates from AGENTS.md. Preparation/commits and
local tests do not publish or activate any feature.

Completion record for this work round: `docs/work-report-2026-11-01.md`. Six bug
branches and the feasible customer foundations are locally committed. Thirteen
branch merge simulations are conflict-free and exactly match the combined test
tree. Main remains unchanged. Billing, hosted email/Storage acceptance and signed
production delivery still require the explicit inputs documented in that report.
