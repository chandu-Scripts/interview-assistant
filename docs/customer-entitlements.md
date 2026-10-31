# Customer access foundation (F2)

This is an undeployed foundation for server-owned customer access. It does not take payments,
choose pricing, fund AI usage, or grant a paid subscription automatically. The current bundled
development Auth project remains auth-only. Existing personal source mode is preserved.

## Public configuration and desktop behavior

`assets/auth.json` supports these public fields in addition to the URL and publishable key:

```json
{"environment": "production", "enforce_access": true}
```

`environment` is `development` or `production`. Missing fields retain `development` and `false`
for the existing development config. Production with `enforce_access: false`, malformed flag
types, and unknown environments fail validation. Frozen builds ignore environment attempts to
change these flags. Production release tooling must additionally require the approved production
project, delivered email acceptance, signing, and `enforce_access: true`.

Enforced startup validates Auth and requests `customer_access_snapshot` before opening the
account workspace. Missing RPC, network failure, malformed response, wrong user, blocked account,
missing grant and expired access cannot unlock the workspace. No service-role key enters the app.

An allowed response grants a memory-only lease of at most 60 seconds, bounded by the grant,
JWT and time-boxed Auth session expiry. Its desktop deadline starts at request initiation using
the monotonic clock, so time spent in transit does not extend it. A refresh begins after at most
20 seconds. Transient errors retain only the existing lease; denied responses lock immediately.
Late success cannot revive an expired lease. Logout fences callbacks and clears the lease.

There is no network access check for each answer. The Qt timer checks once per second and
uses the existing account-lock teardown to stop capture/answers and revoke local authority.
Account context checks use a pure local lease predicate, so new authority/persistence checks
also deny at the deadline even before the timer runs; expiry latches context revocation.
An unresponsive desktop event loop can delay its visible lock; already-admitted atomic local
mutations can finish. Protected server RPCs always check current database state.
A modified BYOK client cannot be commercially controlled merely
by a desktop gate. Owner-funded provider enforcement and the business model remain pending.

## Database and RPC contract

Migration: `supabase/migrations/20261031220303_customer_entitlements.sql` (created by
Supabase CLI 2.122.0, not applied to a hosted project).

Private tables store account block/version/last-seen metadata, administrator membership,
time-limited capability grants, and admin action audit metadata. All enable RLS, with no client
table writes or direct reads. No resumes, transcripts, answers, credentials, email addresses,
payment data or raw error messages are stored. There are no client-editable roles.

Public functions are security-invoker wrappers. Private definer functions use an empty
`search_path`, verify `auth.uid()`, JWT expiry, non-anonymous status, live `auth.sessions`
ownership/time-box expiry and user ban state. Admin actions additionally require current
server-table membership and an unblocked actor. User/app metadata never grants admin access.
Only explicitly granted authenticated RPCs can run; PUBLIC/anon function execution is revoked.

| RPC | Parameters | Result |
| --- | --- | --- |
| `customer_access_snapshot` | none | `user_id`, `allowed`, `reason`, `lease_seconds`, `version`, `is_admin`, server/grant timestamps |
| `customer_require_access` | `capability` | `true` or permission error |
| `customer_admin_list` | `after_user_id` UUID/null, `page_size` 1–100 (default 50) | Metadata array ordered by user UUID |
| `customer_admin_set_block` | `target_user_id`, `blocked`, `reason_code`, `expected_version` | Updated block/version |
| `customer_admin_set_grant` | `target_user_id`, `capability`, `expires_at`, `enabled`, `reason_code`, `expected_version` | Updated grant/version |
| `customer_admin_audit` | `after_id` bigint/null, `page_size` 1–100 (default 50) | Audit array ordered by ID |

Capabilities: `desktop_access`, `cloud_backup`. Reason codes: `support`, `policy`,
`owner_request`, `trial`, `manual`, `correction`. Expiry is a finite timestamp; enabling a grant
requires a future expiry. Revoke supplies the existing expiry with `enabled: false`.
Admin mutations lock the account row, compare `expected_version`, change state and append audit
in one transaction. Version conflicts return SQLSTATE `40001`; refresh and obtain a new version
before retrying. Self-block is rejected. Administrator promotion has no client RPC.

`customer_admin_list` returns every Auth user with UUID, creation time, block/version, last seen,
admin indicator and grants. Newly registered users need not first open the app to appear.
The owner must identify customers by UUID until an explicit identity/display policy is chosen.

The owner role must be provisioned by an approved privileged database operation after the
owner's UUID is confirmed. No admin or grant is automatically seeded by this migration.
Blocking rejects new protected operations and future leases; it does not call the Supabase
Admin API to globally destroy Auth sessions. Every protected server operation must invoke the
private access guard within its transaction; a separate client precheck is insufficient.

## Validation and limits

`supabase/tests/access.test.mjs` executes the migration and role/row checks in actual Postgres
through pinned PGlite 0.5.14. Only Auth schema tables/functions and request claims are synthetic
stand-ins; this is not a hosted Supabase or PostgREST deployment test. Run with Node 22+ and:

```powershell
$env:PGLITE_MODULE = '<pinned installation>/node_modules/@electric-sql/pglite/dist/index.js'
node supabase/tests/access.test.mjs
python -m pytest tests/test_account_access.py tests/test_account_integration.py -q
```

The optional Python engine integration feeds the actual server RPC JSON through the HTTP
adapter and Qt gate, first allowing the customer and then locking after a server block.
Recorded validation: **177 account tests passed** before the local context predicate was added;
the final affected access/integration/storage/composition run passed **94 tests**, including
the actual Postgres engine flow with **45 database assertions**. Ruff, mypy (10 account modules),
and `git diff --check` passed.
Other tests cover A/B isolation, fabricated admin metadata, ordinary-user writes, no-grant,
revoked/expired grants, malformed responses, stale versions, expired/mismatched/deleted sessions,
network outage, clock rollback, slow responses, logout and late callbacks.

Claude's independent source review found no confirmed security blocker. Its narrow expiry
finding is corrected: a JWT/session bound that expires between identity validation and snapshot
computation produces `expired` with a zero lease, rather than an invalid `allowed`/zero pair.
The engine uses a transaction-scoped failpoint after the real identity check to exercise this
boundary deterministically, then rolls the fixture changes back. The post-review engine run
passed **48 assertions**; access/integration tests passed **72 tests**, including the engine-to-Qt
flow. Tests now distinguish unsupported environment variables from actual malformed/disabled
flags loaded from the bundled production config. Ruff and `git diff --check` pass.

Remember-me remains explicitly opted in: denied access revokes local runtime authority but
does not erase that opted-in remembered sign-in. Every restore rechecks current server access.
Clearing remembered login remains an explicit logout/Incognito action. Atomic revocation keeps
the existing persistence lock; slow admitted storage operations can delay Qt teardown. Local
resume/Q&A mutation serialization is handled by the separate F5 account-storage change.

No hosted migrations, deployment, payments, cloud uploads, real users or keys were used.
Hosted advisors, managed-role permissions and real JWT/PostgREST integration still require
an isolated Supabase staging acceptance run. Audit retention/export policy remains a release
decision. Migration rollback must preserve account/grant/audit data; do not drop these tables
to troubleshoot access. Code rollback is branch reversion before any deployment.

## Documentation checked

The current changelog's Data API automatic-grant change was reviewed; access grants here are
explicit. Current function/RLS/session guidance informed the implementation:

- [Supabase changelog](https://supabase.com/changelog)
- [Securing the Data API](https://supabase.com/docs/guides/api/securing-your-api)
- [Database functions](https://supabase.com/docs/guides/database/functions)
- [Row Level Security](https://supabase.com/docs/guides/database/postgres/row-level-security)
- [Auth sessions and sign-out validation](https://supabase.com/docs/guides/auth/sessions)
