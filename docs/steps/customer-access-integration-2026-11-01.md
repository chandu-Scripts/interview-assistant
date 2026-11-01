# Customer access, admin and customer-owned keys

Status: implementation and hosted API, native Qt, and admin-browser acceptance completed
on `feature/customer-access-integration`. Final synthetic-fixture role cleanup is pending.
Main and origin/main remain at `2418dbb`.

## Scope delivered

- Integrated the prepared entitlement and admin branches without merge conflicts.
  No cloud backup implementation, payments, legacy import or release branch was merged.
- Customer accounts use their own provider credentials. Runtime loading, provider
  construction, routing and model selection exclude the personal/friends relay,
  including stale preferences and explicit preferred routes. Customer settings
  explain provider billing and do not construct Shared access. Personal mode retains it.
- Resume and saved-Q&A mutations, including the existing JSON import, are serialized
  against account revocation. Queued writes cannot run after revocation. Already
  admitted writes finish before revocation returns. Incognito is checked again
  immediately before atomic file replacement.
- Implemented and exercised a restricted, opt-in hosted acceptance runner using the actual
  app Auth/Access providers and synthetic fixtures.

The installed Claude CLI reviewed the approach independently, challenged it and
reviewed the final BYOK/library code. Its final source review found no confirmed
blocker in that scope. Independent runner review found premature logout cleanup
confirmation; the follow-up requires actual session denial and excludes natural
JWT expiry from logout evidence. This review is not a substitute for runtime tests.

## Recorded validation

- Full combined desktop regression: **1,409 passed, 2 skipped, 2 deselected** in
  381.13 seconds. The optional live audio/Gemini tests were skipped; real-vault and
  audio-device tests were deliberately deselected. The four added runner-cleanup
  cases landed after this run collected tests and were covered by the final
  27-test runner rerun below. No app behavior changed after the full run; subsequent
  formatting changes were checked to preserve all affected Python syntax trees.
- Native Windows Qt integration: **111 passed**, covering account access,
  composition, Postgres-to-UI integration, customer BYOK and library revocation.
- Actual Postgres engine via pinned PGlite: **48 assertions passed** against the
  integrated SQL migration, with synthetic Auth tables and request claims.
- Admin: **6 Node unit/build tests and 15 isolated Edge browser tests passed**.
  Desktop and mobile screenshots were inspected. No hosted backend was used.
- Focused library regressions: **80 passed**. Ten new cases failed before the
  guards and passed after them. The ignored local semantic model is linked into
  this worktree; it is not a repository change or runtime download.
- BYOK/provider regressions: **183 passed, 1 real-vault test deselected**, followed
  by one additional passing cross-customer credential isolation test. These
  overlap the combined regression run and must not be added together as unique tests.
- Hosted-runner transport regressions: **27 passed** in the final root rerun,
  including successful HTTP logout that leaves a session alive, timeout/cleanup
  failures and expiry-margin guards. These are mocked tests, not hosted acceptance.
- Ruff and mypy passed all **15 changed Python source/tool modules**; both checks
  passed again for the runner after its cleanup correction. Final Ruff checks and
  formatting passed all **21 changed Python source/tool/test files**.

Tests use isolated data and synthetic credentials/providers. No real Credential
Manager operations, microphone capture or paid AI/STT requests were required.
Local logs are ignored under the root `tmp/customer-access-*` and the worktree's
`tmp/byok-static-review/`; browser artifacts are under `website/output/playwright/`.

## Hosted acceptance and remaining work

The Chrome bridge confirmed the signed-in `chandoo-assistant-dev` project,
`lctuyznspcufmzzxbjzv`, was healthy and its SQL Editor accessible. The later read-only
SQL preflight timed out without returning a result; completion is unknown. There
was then no confirmed hosted migration, test-account creation, role promotion or grant
change. The owner subsequently supplied a screenshot showing the complete SELECT
in the original editor with "Click Run to execute your query". Enable Explorer is
an optional feature card, not a browser permission prompt.

One bounded follow-up through Claude Chrome confirmed the project again but timed
out during keyboard input (`Input.dispatchKeyEvent`), followed by a screenshot
injection timeout. Claude reported it never clicked Run or read results, and no
permission denial was returned. Its new tab may contain a partial query and remains
open. Browser automation stopped; the owner was asked to run the complete read-only
SELECT in the original screenshot's tab and share the Results area. CLI credentials
are not available; no private browser storage or credentials were extracted.

The owner supplied the manual result: database `postgres`, private customer schema
absent, public snapshot absent, migration ledger absent, and `auth.sessions.not_after`
present. This confirms the first prerequisite only. The remaining wrapper names,
managed Auth dependencies and applying-role privileges are checked by the read-only
`supabase/tests/hosted-preflight.sql` before any migration.

To avoid further browser-automation retries and a manually maintained migration
ledger, a local Claude MCP connection named `supabase-dev` was configured for the
exact dev project with only database/debugging/docs feature groups. OAuth sign-in
was completed by the owner and a real MCP preflight confirmed the authenticated
connection. The MCP server's organization consent can be broader
than its project-bound endpoint, and must be reviewed by the owner. No PAT, database
password or service-role key was requested in chat. Hosted migration application
must use the supported migration tool and verify its resulting version; if that
version differs from the prepared filename, align the local migration before any
later CLI push. Never invent migration history rows to make a check pass.

The expanded MCP preflight passed: all six wrapper names available, customer schema
absent, migration list empty, required managed Auth columns/functions/roles present,
and the applying `postgres` role had required read permissions. The reviewed migration
was applied once with `apply_migration`; Supabase recorded version `20261031235520`.
The local filename was aligned to that version with an unchanged SHA256 file hash.
The read-only postflight returned the expected four tables, eight private functions,
six public wrappers and one sequence. Complete metadata JSON and all 14 function-body
fingerprints matched the independently generated local baseline. The postflight
query also passed 21 local engine assertions, including privilege/configuration
tampering and changed-body detection. No managed Auth rows were created or changed.

Hosted advisors returned no performance findings. Security WARN findings concern
the pre-existing `public.rls_auto_enable()` function being executable by anon and
authenticated roles; this migration does not define it. Four INFO findings concern
the deliberately private RLS tables with no direct-client policies. No automatic
advisor fixes were applied. Evidence is in ignored root `tmp/claude-customer-mcp-apply.json`,
`tmp/customer-postflight-comparison.json` and `tmp/customer-postflight-expected.json`.

The owner supplied the development key through the masked native prompt. The supported
Auth Admin API created three new confirmed synthetic identities, with credentials saved
only in an ignored fixture file. A separate exact-ID operation assigned the new test
administrator role. No real customer identity was edited; the privileged key was not saved.
The provisioning helper's 27 mocked tests passed. It uses existing dependencies and is
excluded from package inputs; this does not claim a new installer build.

The first hosted runner passed login, ordinary-user denial, grants and account isolation,
then failed at a stale-version block request. It correctly reported an unconfirmed mutation
despite cleaning known state. A read-only inspection proved fixture sessions were gone,
grants disabled, A unblocked, B unchanged, and no matching backend remained.

The deterministic version checks incorrectly used `40001`, which Supabase documents as
causing unlimited retries on affected PostgREST versions. The exact original exception
and hosted version were not captured, so this is a strongly matching documented cause,
not a measured version diagnosis. After independent Claude/Codex proposals and challenge,
the CLI-generated forward migration changed only those two errors to `PT409`.
It was applied once as `20261101003703_customer_conflict_response.sql`; the local filename
matches the hosted ledger. Atomic assertions preserve all other function attributes.
All 14 postflight hashes match the independently generated baseline, and catalog checks
pass. Original migration contents remain unchanged.

Regression results after the correction: **74 runner tests + 27 provisioning tests = 101
passed** in the root combined run; **89 PGlite assertions**; **16 mocked Edge tests + 6 Node
unit/build tests**; **15 native-harness cleanup tests**. Ruff, mypy and syntax checks passed.
An initial attempt to combine tracked and root/tmp tests selected the wrong pytest root
and failed collection; separate explicit-config runs above passed without a product change.

The second real Auth/RPC run passed all **10 acceptance checks**, including exact HTTP
409/PT409 for stale block and grant, no state/audit change, block/unblock, fresh sign-in,
actual grant expiry, old-session rejection and metadata audit. `cleanup_ok` is true.
The original failed result remains preserved. These API checks do not substitute for
the separate native-timer and live-browser evidence below.

Native Windows acceptance passed all five checks using real hosted Auth/RPC and the unchanged
one-second Qt timer in three fresh processes: A blocked during use, B isolated, then A signed
in after unblock and locked at actual grant expiry. A's resume, Q&A and history stayed intact;
B saw none of A's records and did not change A's files. The workspace stayed 960 x 680 logical
pixels. Screenshots of the blocked and expired notices were visually inspected. Audio/LLM
providers and Credential Manager were replaced by isolated stand-ins; no capture or paid model
call occurred. All worker and coordinator sessions were revoked and cleanup passed.
Evidence: ignored `tmp/customer-native-2zdyzoqj/result.json` and its widget captures.

The isolated live Edge admin run passed all four checks: real admin login/exact fixture search,
Grant/Block/Unblock/Revoke, ordinary-user rejection, and UI logout that clears browser state and
rejects the old JWT. The generated local site used only development public configuration and
the generated CSP; the user's Chrome profile was untouched. Desktop/mobile screenshots were
inspected. No hosted website deployment was made.

The first two browser attempts stopped at the saved-notice assertion and both confirmed cleanup.
Safe diagnostics on the second showed HTTP 200 for the grant: the harness read the prior notice
before the asynchronous list refresh completed. Correcting response/render synchronization made
the third run pass. There was no app-code change for that harness race. Results are retained in
ignored `tmp/customer-admin-hosted-{first-attempt,second-attempt,final-attempt}.json`; successful
screenshots are under `tmp/output/playwright/customer-admin-live-*`.

Final temporary-role cleanup is still pending. The exact prepared cleanup script checks
fixture sessions/grants/block state and removes only the new test-admin membership. Supabase
requested server confirmation, which the noninteractive Claude client cancelled immediately;
the tool explicitly reported "Nothing was run." After the owner renewed expired MCP OAuth,
independent read-only verification confirmed all three fixtures have zero sessions and enabled
grants and are unblocked; only the synthetic administrator still has membership. Audit counts
remain A: 30, B: 2, admin: 0.

An interactive confirmation subsequently expired before execution. At the owner's instruction,
a fresh visible Claude helper first repeated the read-only checks and submitted the exact
cleanup once through normal Supabase confirmation. That confirmation also expired before
execution; the helper stopped without retrying. Role removal remains pending. No
confirmation bypass or alternate mutation path was used. The prepared ignored script is
`tmp/customer-final-cleanup.sql` in the root checkout. Synthetic identities and audit metadata
are intentionally retained; this cleanup does not delete Auth users or user data.

A further owner-coordinated attempt used Claude auto mode with normal server confirmation.
It returned no cleanup result; the exact pending helper was stopped before switching to a
manual SQL Editor handoff. The subsequent independent read-only check again confirmed zero
sessions/enabled grants, unblocked fixtures, unchanged audit counts, and the temporary admin
membership still present. Evidence: ignored `tmp/claude-customer-cleanup-verify.json`.
Auto mode was separately smoke-tested successfully; it does not supply Supabase confirmation.
No cleanup mutation is claimed. The owner can run the prepared guarded script in the
development SQL Editor, after which a fresh read-only check must verify role removal.

Latest advisors reported no performance findings. The pre-existing `public.rls_auto_enable()`
WARN findings remain; Auth also reports leaked-password protection disabled. Four INFO
findings concern intentionally private RLS tables. No settings or unrelated functions
were changed to suppress findings. These remain review items before commercial release.

The normal bundled development configuration remains auth-only. Access enforcement
must be explicitly enabled in an isolated acceptance bootstrap and in the eventual
approved release. Custom SMTP is off; signup/recovery email delivery, domain setup,
production configuration, code signing and a new installer remain separate work.
Customer app pricing is still undecided; provider usage is paid by customers.

Main merge and push require the owner's separate go-aheads. The current local
results do not justify calling the commercial release ready.
