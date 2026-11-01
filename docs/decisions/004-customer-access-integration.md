# Customer access, admin and BYOK integration

Date: 2026-11-01. The owner authorized the next step: integrate the prepared access
and admin features, verify development login/blocking with test identities, and
check customer-owned provider credentials. Main merge and push remain separate
approval gates. Work starts from main `2418dbb` on `feature/customer-access-integration`.

## Approach and independent review

Integrate F2 access enforcement and F1 admin UI without cloud backup, payments,
legacy account transfer or a production release. Git merge checks found no conflicts.
Codex, delegated reviewers and the installed Claude CLI independently reviewed the
approach. Claude withdrew an unsupported claim of inevitable conflicts after the
actual common-base and merge-tree evidence was supplied.

The account context, not an editable preference, determines customer BYOK policy.
Customer startup must ignore relay credentials/settings, provider and routing
factories must exclude the relay, and customer settings must not construct Shared
access. Personal/friend relay behavior stays available. Hiding the tab alone was
rejected because stale preferences or explicit route selections could still route
through the owner-funded service. Provider identity verification cannot prove who
owns or pays for a pasted key.

Extract only the atomic local-library mutation guards from F5. Include existing
practice JSON import, resume imports, saves and deletes; exclude the separate
legacy-account-transfer feature. This closes the write-versus-revocation gap when
F2 is integrated without all of F5. Preserve the context-then-library lock order.

## Development acceptance

Use only `chandoo-assistant-dev`, project `lctuyznspcufmzzxbjzv`. The Chrome bridge
observed an authenticated, healthy project and an accessible SQL Editor. CLI auth
is currently unavailable. Custom SMTP is off. Do not disable email confirmation or
claim hosted signup/recovery delivery works. Use separately identified synthetic
confirmed test accounts for access tests, without emailing real people.

Review and apply only the existing additive entitlement migration after checking
that its objects do not already exist. No existing user is promoted to admin and no
existing customer data is modified. Provision only an explicitly identified test
administrator, plus ordinary test accounts A and B. Test grants have finite expiry.
Keep test credentials and tokens out of committed files and reports.

Desktop and admin must target the same development project. Run the desktop with
an isolated data root and an explicit development bootstrap with access enforcement
enabled; keep the ordinary bundled config unchanged. Browser tests use a separate
profile. Use customer public clients for RPCs; no privileged key enters either UI.

Verify no-grant/expired/blocked denial, allowed login, admin denial for ordinary
users, account isolation, grant preservation, stale-version conflicts, audit
metadata, active-session blocking, unblock and fresh login, logout and stale-session
denial. The access lease is at most 60 seconds; blocking denies protected operations
and future leases but does not globally delete Auth sessions. Use synthetic provider
keys for BYOK checks; no paid inference or microphone capture is needed.

## Validation, limitations and rollback

Run focused Python regressions, actual Postgres engine assertions, admin browser
tests, static checks, and combined tests after integration. Distinguish mocked,
engine-only, native UI and hosted results. If dashboard permissions or test-account
provisioning prevent hosted checks, finish independent local work and state the exact
missing action instead of substituting mocks for hosted evidence.

Before deployment, code can be reverted by branch. After a development migration,
retain access/grant/audit tables and use a forward correction; do not drop data to
undo a problem. Remove test grants/admin membership and end test sessions after
acceptance, preserving an accurate test record. No domain purchase, SMTP change,
production deployment, installer release, main merge or push is part of this step.

Current references checked: [session validation](https://supabase.com/docs/guides/auth/sessions),
[Data API grants](https://supabase.com/docs/guides/api/securing-your-api),
[Free email-template changes](https://supabase.com/changelog/46599-changes-to-email-template-customisation-on-free-tier),
and [TLS cipher changes](https://supabase.com/changelog/cbc-tls-ciphers-deprecated).
The current Python/Edge clients use modern TLS; no custom legacy cipher settings
are added. The PostgreSQL upgrade notice concerns extensions this migration does
not create or modify.

## Implementation review and hosted status

The final installed-Claude CLI review found no confirmed blocker in the BYOK and
library-mutation changes. It inspected source/tests only; this is not hosted test
evidence. Independent review of the optional hosted runner found that an HTTP-success
logout response was being treated as confirmed cleanup before old-session denial
was checked. The runner must verify revocation and explicitly fail cleanup if the
session remains valid. Token-expiry checks must also prevent natural expiration
from being mistaken for successful logout.

The Chrome bridge's read-only SQL preflight timed out without a result. Its
completion was unknown. At that point no schema migration, fixture provisioning, role
assignment or hosted access acceptance was confirmed. The owner's later screenshot showed the
complete SELECT had not been run; Enable Explorer was only an optional feature card.
One bounded follow-up reported browser keyboard-input and screenshot timeouts before
Run, with no permission denial. Browser automation stopped and a manual result from
the original, complete read-only query was requested. Dependent hosted operations
were pending that result. Local regressions and the opt-in runner are complete. See
`docs/customer-access-acceptance.md` for the restricted fixture contract and
`docs/steps/customer-access-integration-2026-11-01.md` for the recorded results.

## Hosted database and fixture provisioning follow-up

The owner returned the manual preflight and completed OAuth for the project-bound
Claude `supabase-dev` MCP connection. The expanded read-only preflight passed, then
the existing migration was applied once using the supported migration tool. Version
`20261031235520` is recorded remotely and is now the local filename. Catalog structure,
permissions and all 14 function-body fingerprints exactly match the local baseline.
No manual migration-history changes or managed Auth row changes were made.

Claude and Codex independently proposed synthetic-fixture provisioning, then challenged
failure handling in a second review. The chosen helper lives in `scripts/`, outside
packaged application inputs, and uses the existing PySide6 dependency for a masked
local secret prompt. Dashboard entry remains an alternative if no secret is supplied.
Direct managed-Auth SQL was rejected. No secret travels through chat, browser, shell
arguments, environment, fixture JSON or logs; modern keys use the `apikey` header.

The opt-in helper targets only the fixed development host, reserves a new ignored
output path before network work, creates three confirmed synthetic identities through
the supported Auth Admin API, and saves the existing runner's fixture contract.
Administrator membership is a separate operation limited to the returned new test
admin UUID. No existing identity is promoted or edited. Random passwords and fixture
files remain local; they never enter review prompts or source control.

Controlled failures roll back only proven new identities from that invocation and
verify each deletion with exact-ID Auth `user_not_found`. An ambiguous request can
leave an unknown creation; report it explicitly, never guess an ID or claim cleanup.
A password-free ID journal supports manual reconciliation after process termination.
Python cannot guarantee wiping immutable strings from memory. Offline transport tests
cover request bounds, invalid input, cancellation, partial failure and cleanup proof;
they do not substitute for the pending live acceptance.

References: [API keys](https://supabase.com/docs/guides/getting-started/api-keys) and
[supported user creation](https://supabase.com/docs/reference/javascript/auth-admin-createuser).
