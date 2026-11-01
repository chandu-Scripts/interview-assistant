# Customer admin website (F1)

Undeployed administrator UI for the server-owned contract in
`docs/customer-entitlements.md`. Development integration and synthetic hosted acceptance
are recorded in `docs/steps/customer-access-integration-2026-11-01.md`.
Production deployment, payments and customer document access are outside this step.

## Decision and boundaries

The existing static download website gains a dedicated `/admin/` page in the same navy/cyan
glass style. A locally bundled, exact-pinned `@supabase/supabase-js` 2.118.0 handles email/password
authentication and RPC calls. The website ships an empty public configuration and cannot sign
in until a hosted project and publishable key have deliberately been configured.

The first successful `customer_admin_list` RPC establishes the admin view. Every later RPC
independently checks current server-owned administrator membership, block status and Auth
session state through F2. User-editable metadata is never consulted for authorization.
The client has no service-role key, table access, admin promotion, account deletion or billing UI.
The SDK's `auth.admin.signOut` method is used only to revoke the caller's own current session
using that session's JWT and local scope; it does not grant administrator authority.

Direct privileged database access and a client-side admin flag were rejected. A new server,
framework and separate role system add no value for the existing narrow RPC surface.
This remains a prepared foundation, not proof of production readiness.

## Operator workflow

- Sign in. The password field clears immediately after submission. A failed sign-in or rejected
  admin RPC displays a fixed message without provider/database error details.
- Customers are paged in UUID order, 25 at a time. Exact UUID search requests the predecessor
  UUID as a keyset lower bound and filters the single response for the exact target. It does
  not scan all accounts. Zero UUID is handled explicitly. Email/name search is unavailable.
- Review block status, administrator status, last seen, creation time and existing Desktop /
  Cloud backup grants. An unblocked account still needs a live grant; blocking overrides grants.
- Block/unblock, grant and revoke display the full target UUID and require typing that exact ID,
  choosing one fixed reason, then explicitly confirming. New grants require a future expiry
  entered in the operator's local time and sent as UTC. Revoke preserves the existing expiry.
- Mutations include the displayed `expected_version`. HTTP 409 / SQLSTATE `PT409` causes a refresh and a
  new confirmation, never an automatic retry. A connection error can mean a change succeeded;
  the page clears stale records and asks the operator to refresh before retrying.
- Activity shows paginated action metadata, actor and target UUIDs, reason, version, expiry and
  timestamp. Oldest events appear first, matching the existing ascending-ID RPC contract.
  No resumes, transcripts, answers, keys, customer emails or payment details appear.

Client response validation rejects unsafe JavaScript integers for versions/audit IDs and malformed
roles/grants. If IDs ever exceed the safe integer range, a string-based server contract is needed.
Self-block is disabled in the UI and independently rejected by F2.

## Session lifecycle

The SDK uses a dedicated memory-only storage adapter with a unique per-login storage key.
`persistSession: true` is required for this SDK to honor a custom adapter; it does **not** mean
browser persistence here. No token, password or customer records go into localStorage,
sessionStorage or cookies. Automatic background refresh and URL session import are disabled.
SDK request-time refreshes may still occur, but never extend the original admin window.

The window ends at the earlier of JWT expiry and 15 minutes. Deadlines use monotonic time plus
the original wall-clock expiry; every protected request checks them. Returning to a background
tab also checks expiry. Browser suspension can delay the visible clear until the tab resumes.
Closing/reloading the page requires fresh sign-in; recovery links cannot open the workspace.

Sign-out/expiry synchronously clears tables, action state and credentials from the store,
revokes future store writes, aborts pending requests and invalidates callbacks. A late SDK
refresh/login completion cannot restore the old session. Current-session remote sign-out is
best effort with a five-second timeout; local removal never waits for the network. Disposing
the SDK alone was insufficient because its documented lifecycle can allow late storage writes.
Remote session deletion is not guaranteed during a network failure or abrupt tab close.
F2 server checks remain authoritative for every operation.

## Build and local verification

Use Node 22+ (validated with Node 24.18.0), then from `website/`:

```powershell
npm ci --ignore-scripts
npm run build:admin
npm run preview
npm run test:admin
```

Open `http://127.0.0.1:4173/admin/`. Admin-only build does not need an installer or modify the
download release manifest. The full website build still requires the matching Windows installer;
its existing development-only release metadata has not been updated by this feature.

The SDK and build/test dependencies are exact-pinned with a committed lockfile. Browser tests
launch isolated headless Microsoft Edge through Playwright, never the user's Chrome profile.
All Auth/RPC traffic is intercepted using synthetic records; unexpected external requests are
aborted. The tests cover success and negative paths, not hosted Supabase integration.
Local screenshots are ignored under `website/output/playwright/`.

Recorded local validation: **15 isolated browser tests and 6 Node unit/build tests passed**.
JavaScript syntax checks, `git diff --check` and `npm audit --omit=dev` passed (zero reported
runtime dependency vulnerabilities at validation time). Desktop and 390-pixel mobile layouts
were visually inspected using synthetic data. A production installer/download build and a
hosted Supabase round trip were not run as part of F1.

Integration follow-up: the `PT409` regression pass now includes **16 mocked Edge tests
and 6 Node unit/build tests**. A separate isolated Edge run against the development project
passed real administrator login, exact fixture search, Grant/Block/Unblock/Revoke, ordinary-user
admin denial, and logout with old-JWT rejection. Desktop and mobile screenshots were inspected.
The live harness needed a timing correction: closing the action dialog precedes the list refresh
and saved notice. It now waits for the new mutation and refresh responses before checking that
notice. This did not require an application change. Every attempt verified cleanup.
See the integration step report for scope, evidence and final test-role cleanup. The website
itself remains undeployed and its committed public configuration remains blank.

The generated `dist/vercel.json` permits connections only to the configured hosted Supabase
origin, uses local scripts/styles, forbids framing and object embeds, and disables admin caching.
The preview server serves the generated headers. Configuration is validated before any build
copy; secret keys, legacy unclassified JWT keys, non-HTTPS hosts and extra fields are rejected.
The committed `public/admin/config.json` is deliberately blank.

## Required before hosting

1. Validate F2 migration/roles/RPCs against an isolated hosted staging project and real JWTs.
   Provision the owner's confirmed UUID through an explicitly approved privileged operation.
2. Configure only `url` and `publishableKey` in `website/public/admin/config.json`, rebuild and
   inspect `dist/vercel.json` for the exact approved origin. Never put a service key in this file.
3. Test actual admin/nonadmin sessions, expiry, block/revoke effects and access grant timing in
   staging. Confirm the hosting access policy and obtain deployment approval. This work does
   not replace the desktop release signing and email-delivery acceptance requirements.

Rollback before deployment is a branch reversion. After deployment, remove the admin route or
revert the website artifact; do not drop F2 account/grant/audit tables. No automatic role seeding
or entitlement changes are part of a website rollback.

## References checked

The current [Supabase changelog](https://supabase.com/changelog) was reviewed, including upcoming
TLS cipher removal; this browser client uses the browser TLS implementation. SDK initialization,
storage and logout behavior were checked against official docs and the installed pinned source:

- [JavaScript client initialization](https://supabase.com/docs/reference/javascript/initializing)
- [Current-session sign-out](https://supabase.com/docs/reference/javascript/auth-signout)
- [SDK disposal lifecycle](https://supabase.com/docs/reference/javascript/auth-dispose)
