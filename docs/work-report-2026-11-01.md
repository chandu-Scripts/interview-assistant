# Feature branch work report, 1 November 2026

> Historical work-round evidence. Later work integrated customer entitlements/admin
> and completed recorded hosted development acceptance. The current merge/push,
> branch and deployment state is in [PROJECT_STATUS.md](PROJECT_STATUS.md).
> Statements below about untouched main or pending migrations refer to their dated phase.

Work was performed in separate local Git worktrees with three delegated agents and
independent reviews through the installed Claude CLI. At the end of that work round,
main remained at `5d111fc` and no branch had been merged or pushed. No live service,
installed app, customer record, personal credential or domain setting was changed.

The existing customer-login foundation was preserved as `683efd1` on
`feature/account-login`. The branches below depend on it. Their commits are local.

## Bug fixes ready for review

| Branch | Head | Result |
| --- | --- | --- |
| feature/saved-answer-direction | 4047580 | Reversed source/destination questions cannot retrieve the opposite saved answer; semantic and cached paths check direction. |
| feature/stream-completion | 597a84b | Missing provider completion signals stay incomplete; Stop and presentation cannot leak a cancelled answer into conversation memory. |
| feature/stale-transcription | 599773a | Capture epochs fence old audio, previews and queued callbacks; a failed preview warmup reports its fallback on the next capture. |
| feature/history-search-actions | a4bd8d7 | Transcript, export and confirmed deletion target the selected search result's session. |
| feature/relay-budget-reservations | e2589aa | Atomic durable reservations bound concurrent requests; ambiguous cancellation/crash costs remain charged; token rotation and input limits are checked before sending. |
| feature/credential-removal | c601239 | Vault failures cannot claim removal; purge preserves retry inventory; missing relay tokens and failed key rollback show accurate results. |

Relay schema 2 is prepared, not applied to the hosted relay. Unknown model prices
fail before upstream spending. Conservative estimates can temporarily consume more
budget than actual usage; the admin view exposes them for reconciliation. The relay
still requires one shared SQLite database on one Fly machine.

## Prepared customer features

| Branch | Head | Result and boundary |
| --- | --- | --- |
| feature/customer-entitlements | 0653008 | Server-owned block/grant/audit tables and RPCs; desktop access leases bounded to 60 seconds, refreshed without per-answer requests. Hosted migration pending. |
| feature/customer-admin | ae18f68 | Glass admin website with UUID search, block/unblock, access grants, audit pages, confirmation and version checks. Memory-only login; public configuration blank; not deployed. |
| feature/legacy-import | b9768c4 | Explicit preview, category selection, portable export and append-only import into an account. Originals preserved; safe retry and privacy/logout fences. |
| feature/cloud-backup-sync | 92dd2e1 | Explicit private cloud upload, preview, import and delete with five bounded slots. Opt-in each visit; same-account restore. Manual snapshots only, no continuous synchronization. Hosted Storage pending. |
| feature/production-release | e1b9887 | Read-only release gate rejects development config, missing hosted evidence, unsigned binaries, private files and mismatched full-package hashes. No release built or published. |
| feature/auth-email-acceptance | 7230cbf | Signup/recovery OTP templates and a concrete hosted test runbook. Evidence is deliberately marked pending; no emails sent. |
| feature/payments | 7475f9d | Billing decision and fulfillment requirements recorded. Checkout and subscriptions are not implemented or activated. |

The admin branch includes the entitlement foundation. The cloud branch includes
both entitlements and local transfer support. Equivalent dependency commits were
cherry-picked only into these isolated feature branches. This did not merge main.

## Verification

A temporary combined checkout at `tmp/validation-bugs` contains the proposed code.
Git merge-tree simulations for all 13 feature branches produced no conflicts. The
simulated merge tree exactly matches the staged tree used for combined validation.
The simulation created only temporary Git objects, without moving any branch.

Final combined desktop result: **1,468 passed, 4 skipped, 2 deselected** in 205.92
seconds. The two deselected tests access real Credential Manager/audio hardware.
The skipped cases were optional hosted Gemini, live spoken-audio, Windows symlink
permission and the opt-in Postgres-to-Qt integration. That Postgres integration was
then run explicitly on the combined checkout and passed. No hosted test was run.
Combined Ruff passed. Mypy passed for all 39 changed source/tool modules.

Other completed checks (some overlap with the desktop suite):

- 111 relay server tests passed with synthetic HTTP and database fixtures.
- 48 entitlement assertions passed in actual PGlite Postgres; Auth schema/claims
  were synthetic. This is not a hosted Supabase acceptance result.
- 22 Storage policy assertions passed in actual PGlite Postgres with synthetic
  Auth/Storage schemas on the final combined tree, including cross-account and
  broad-policy denial.
- 15 isolated Edge browser tests and 6 Node unit/build tests passed for admin.
  Desktop and mobile screenshots were inspected. All service traffic was mocked.
- 40 cloud/UI/composition tests passed; 9 UI tests also passed on native Windows Qt.
- 42 release-gate tests passed using fake artifacts and injected signature results.

Claude's reviews found real follow-up cases in credential cleanup, cancelled
conversation memory, preview warmup and transfer recovery. They were reproduced or
checked against source, fixed and retested. Reviews are not a substitute for tests.

## Required before production

1. Customer-owned AI keys were selected on 2026-11-01; customers pay providers
   directly. Choose a merchant service, currency, prices and terms for app access.
   No paid plan was invented. See `decisions/003-customer-owned-api-keys.md`.
2. Set up a production Supabase project, owner UUID/admin provisioning, SMTP sender
   and the deferred domain. Apply reviewed migrations in staging and verify real
   JWT/PostgREST/Storage behavior with two dedicated test accounts.
3. Complete hosted signup, email delivery, OTP expiry, recovery, login/restore,
   block/unblock and isolation acceptance. Record exact release artifact hashes.
4. Configure code signing, create the candidate package and installer, and run the
   production release gate. Agree data retention, deletion and operational recovery.

The current public desktop configuration still targets the development project.
Prepared access enforcement and cloud UI do not silently activate in that auth-only
development configuration. Backup payloads are private server snapshots, not
end-to-end encrypted. Modified customer-owned-key clients cannot be fully controlled
through a desktop license gate. Live audio/provider latency, actual removable-drive
hardware and power-loss recovery were not tested in this round.

The original review recommended the account-login foundation, six bug branches,
entitlements, local import, admin, cloud and release/email preparation. The owner's
subsequent approval is being applied to the six tested bug fixes and their required
login foundation. The remaining production features stay on their branches. Payments
contains planning only; the later customer-owned-key decision supersedes its open
billing-model question. GitHub push still requires separate approval.

## Approved local merge completed

The owner approved the tested bug-fix merge and selected customer-owned API keys.
Main now contains the account-login prerequisite, all six bug-fix branches, the
work reports and the customer-owned-key decision. Admin, entitlements, local import,
cloud backup, hosted email, release preparation and payments feature branches were
not merged. Their results above describe prepared branch code, not current main.

All approved merges completed without conflicts. The previous main is retained at
`backup/before-bug-merge-20261101` (`5d111fc`). No branches were deleted. GitHub push,
hosted migrations/deployments and installed-app/website updates were not performed.

The first desktop run on the narrower merged tree found one real UI timing issue:
1,309 tests passed and a budget-warning display test failed. A pending answer
refresh erased the already displayed warning, although the budget correctly blocked
the request. Two deterministic UI cases reproduced it. Codex and the installed
Claude CLI independently reviewed the cause; `fix/overlay-error-render` (`ea38aa8`)
added two timer stops and three regression cases, then merged into main. Details:
`docs/bugfixes/B7-overlay-error-render.md`. The existing budget test was unchanged.

Final code validation at merge commit `e35f8c0`:

- Desktop: **1,313 passed, 2 skipped, 2 deselected** in 214.54 seconds. Skips were
  optional live audio/Gemini; real Credential Manager and loopback tests were
  deliberately deselected. The count differs from the earlier 1,468-test combined
  run because production-feature tests are not part of this approved merge.
- Relay: **111 passed** on the merged relay code; one dependency deprecation warning.
  No relay code changed after that run.
- Focused app/cost/continuation/stream suite: **203 passed** after the timer fix.
  Native Windows Qt regressions: **5 passed** (overlapping tests).
- Ruff passed for desktop/tools and relay; mypy passed for all 48 changed Python
  source/tool modules, with overlay checked again after the final correction.
- Whitespace checks passed. All seven approved feature heads were verified as main
  ancestors, and all seven deferred production-feature heads remained excluded.

Logs are local ignored files under `tmp/merge-*.log`. All tests used isolated data
and synthetic providers. The final report commit changes documentation only.
Customer key ownership is recorded in AGENTS.md and decision 003; pricing and the
remaining commercial-release implementation/acceptance work are still pending.
