# Customer access, admin and customer-owned keys

Status: local implementation integrated on `feature/customer-access-integration`;
hosted acceptance is pending. Main and origin/main remain at `2418dbb`.

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
- Prepared a restricted, opt-in hosted acceptance runner using the actual app
  Auth/Access providers and synthetic fixtures. It has not been executed live.

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
is no confirmed hosted migration, test-account creation, role promotion or grant
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

Provision only dedicated synthetic fixtures, then run the hosted Auth/RPC tests plus
visible desktop/admin acceptance. A masked local provisioning helper is being prepared
and reviewed; no privileged Auth secret has been supplied or fixture created. Verify fresh login,
blocking and unblock, logout, ordinary-user denial and isolation. Clean up fixture
grants, sessions and temporary admin membership and check hosted advisors.
The runner's Auth/RPC/lease coverage does not prove Qt timer-driven visible lock
or hosted browser behavior.

The normal bundled development configuration remains auth-only. Access enforcement
must be explicitly enabled in an isolated acceptance bootstrap and in the eventual
approved release. Custom SMTP is off; signup/recovery email delivery, domain setup,
production configuration, code signing and a new installer remain separate work.
Customer app pricing is still undecided; provider usage is paid by customers.

Main merge and push require the owner's separate go-aheads. The current local
results do not justify calling the commercial release ready.
