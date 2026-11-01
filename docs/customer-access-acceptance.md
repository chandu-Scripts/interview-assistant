# Customer access hosted acceptance runner

`scripts/verify_customer_access.py` is prepared for an explicitly authorized development test.
It has not been run live during preparation. It accepts only the approved development project
`https://lctuyznspcufmzzxbjzv.supabase.co`. No production project, account creation/deletion,
signup email, password changes, paid AI provider, audio or desktop workspace is involved.

## Fixture contract

Provision three dedicated, confirmed email/password Auth accounts separately: one administrator
and two ordinary customers. Confirm their UUIDs and provision the administrator's membership
through an approved privileged operation. This runner does not create identities or roles.
The F2 entitlement migration and authenticated RPC permissions must already be installed.

Supply an explicitly selected, **git-ignored JSON file**. Do not put passwords in command-line
arguments, `.env`, ordinary environment variables, tracked files or review/chat logs. The file
must contain exactly these fields (examples below are placeholders, not usable credentials):

```json
{
  "project_url": "https://lctuyznspcufmzzxbjzv.supabase.co",
  "publishable_key": "sb_publishable_PUBLIC_KEY",
  "admin": {
    "user_id": "11111111-1111-4111-8111-111111111111",
    "email": "chandoo-access-test-admin-RUN@example.test",
    "password": "GENERATE_AND_STORE_LOCALLY"
  },
  "customers": [
    {
      "user_id": "22222222-2222-4222-8222-222222222222",
      "email": "chandoo-access-test-a-RUN@example.test",
      "password": "GENERATE_AND_STORE_LOCALLY"
    },
    {
      "user_id": "33333333-3333-4333-8333-333333333333",
      "email": "chandoo-access-test-b-RUN@example.test",
      "password": "GENERATE_AND_STORE_LOCALLY"
    }
  ]
}
```

Replace `RUN` with a lowercase alphanumeric/hyphen suffix and use a controlled test domain.
All three emails must be lowercase and begin `chandoo-access-test-`. UUIDs/emails must be
distinct; customer array position defines A and B. Passwords are 12–1024 characters. Only
publishable keys are accepted. Unexpected fields, wrong hosts, ordinary `.env` files,
nonignored files, mismatched authenticated identity/email, wrong roles, blocked customers or
preexisting active grants cause refusal. Baseline active-grant checks use server time.
The administrator must be unblocked; no desktop grant is required for administrative RPCs.

## Commands

From the integration worktree, after explicit approval for hosted fixture mutations:

```powershell
$env:PYTHONPATH = "$PWD/src;$PWD"
& C:/Users/tcs91/Desktop/KCPD/.venv/Scripts/python.exe -m scripts.verify_customer_access --live --fixtures C:/Users/tcs91/Desktop/KCPD/tmp/customer-access-fixtures.json
```

Without `--live`, the command refuses before reading credentials or connecting. It never reads
provider credentials, `.env` or default account storage. HTTP redirects and environment proxy
inheritance are disabled. Its only network destinations are the approved project's Auth and
allowlisted RPC endpoints. The JSON result contains fixed check/failure labels; no credentials,
session tokens, emails, raw provider errors or audit rows are printed.

### Optional local fixture provisioning

`scripts/provision_customer_access_fixtures.py` prepares the three new synthetic identities
through the supported Auth Admin API. It is development tooling, outside the packaged app.
It reads only the bundled public Auth configuration and accepts the exact development host.

```powershell
$env:PYTHONPATH = "$PWD/src;$PWD"
$env:QT_QPA_PLATFORM = 'windows'
& C:/Users/tcs91/Desktop/KCPD/.venv/Scripts/python.exe -m scripts.provision_customer_access_fixtures --live --fixtures C:/Users/tcs91/Desktop/KCPD/tmp/customer-access-fixtures.json
```

The JSON and adjacent `customer-access-fixtures.created-ids.jsonl` must not already exist;
both must be git-ignored. A masked native dialog accepts the development `sb_secret_` key
in memory only. Never paste that key into chat, command arguments, environment, the fixture
JSON or either application. No existing Auth account is edited and no email is sent.
No admin membership or grant is assigned; that separate step targets only the newly returned
test administrator UUID. The fixture file is verified through the runner's existing loader.

On controlled failure, only proven newly created IDs are rolled back. Exact-ID Auth
`user_not_found` confirms removal; an ambiguous creation or unsuccessful deletion stays
explicitly unresolved. The flushed journal contains UUIDs only and is never automatically
read for deletion. Process termination can prevent cleanup; it requires manual reconciliation.
The helper cannot guarantee secure erasure of immutable Python strings from memory.
No terminal-input fallback is provided when the native prompt is unavailable.

## Covered sequence

1. Sign in and validate all three identities through the existing app Auth provider. Check
   exact UUID/email, current admin/customer roles, an unblocked customer baseline and no active
   grants. Capture customer metadata and the audit cursor, with a bounded metadata-only scan.
2. Missing desktop grants deny both customers. Ordinary users cannot list/audit administrators;
   customer B's explicit attempt to block fixture A must also be denied by the actual server.
3. Grant A Cloud backup and Desktop. A's real access snapshot admits the existing desktop
   `AccessLease`; B remains denied. Both grants survive, and both protected capability checks pass.
4. A stale version fails without changing the account. Blocking A rejects protected operations
   and its next snapshot clears the existing lease. Unblocking preserves the valid grant.
   A fresh password sign-in then validates UUID/email, a distinct session token, allowed
   access snapshot, a new usable lease and both protected capabilities. The original and
   fresh sessions are tracked separately until each logout is confirmed.
5. Revoking Desktop leaves Cloud backup intact. A new short Desktop grant expires using server
   time and a six-second wait; snapshot and protected access must deny it.
6. Regrant Desktop, prove access, sign out A's own Auth session, then require the old JWT's
   protected RPC/snapshot to fail. Check B's grant/version metadata stayed unchanged. Verify
   this administrator's new action audit records contain only the expected fixture targets and
   allowed metadata, without printing any rows.

This verifies the real app providers and lease adoption/clearing. It does not launch the Qt
workspace, measure its timer-driven visible lock, or replace the separate UI/desktop acceptance.
Unblock is checked using both A's existing session and a fresh password sign-in.
Audit pagination is bounded to 2,000 entries per scan; a larger existing audit requires a more
targeted fixture setup. No unrelated account metadata is retained in the result.

## Cleanup and limits

Every attempted fixture mutation is tracked before sending, including ambiguous failures.
`finally` resets touched fixture block status to false, disables only test-touched grants and
rechecks the state. Previously disabled grants retain their baseline expiry; newly created
grant rows remain disabled because the API provides no delete-grant operation. Expired enabled
baseline grants are also left disabled. Customer B's untouched grants are not modified.
Audit entries and last-seen/version updates intentionally remain as test evidence.

Cleanup uses fresh optimistic versions and does not blindly retry a conflict. Run only one
acceptance instance for these fixtures at a time. All created Auth sessions are signed out and
their old JWTs must produce an invalid-session error from the access snapshot. A successful
logout response alone does not confirm revocation; a no-grant or blocked snapshot still means
the session is live. JWTs must have at least 60 seconds remaining before and after that proof
to exclude natural expiry. Failed exercise logout verification is retried during cleanup.
Tokens and lease state are discarded from memory. Every cleanup/sign-out failure is explicit
in the result and makes the command fail. If cleanup fails, inspect and reset the designated
fixtures through the approved admin path before another run. Forced process termination,
power loss or repeated cancellation can prevent cleanup; this runner is not a transaction
across Auth and multiple RPC calls. A logout visibility delay fails acceptance rather than
claiming immediate revocation without evidence.

## Offline regression verification

```powershell
$env:PYTHONPATH = "$PWD/src;$PWD"
& C:/Users/tcs91/Desktop/KCPD/.venv/Scripts/python.exe -m pytest tests/test_hosted_customer_access.py -q
& C:/Users/tcs91/Desktop/KCPD/.venv/Scripts/python.exe -m ruff check scripts/verify_customer_access.py tests/test_hosted_customer_access.py
& C:/Users/tcs91/Desktop/KCPD/.venv/Scripts/python.exe -m mypy --explicit-package-bases scripts/verify_customer_access.py tests/test_hosted_customer_access.py
```

Transport tests use the real SDK/Auth/Access adapter paths with synthetic HTTP responses.
They cover successful acceptance, fixture/host/key/role/baseline refusal, explicit live opt-in,
ordinary-user write-denial failures, cancellation, ambiguous committed grants, cleanup failures,
sign-out failures, successful no-op logout, insufficient JWT validity around revocation proof,
metadata-only audit responses and restoration of disabled-grant expiry.
