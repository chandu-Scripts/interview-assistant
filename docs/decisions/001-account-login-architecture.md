# Customer account login architecture

Date: 2026-11-01

Status: recommended design after Codex repository review and two real read-only
Claude CLI rounds. Application implementation and hosted-flow validation have not
started. This record does not change runtime behavior or cloud configuration.

## Scope and outcome

Build native email/password signup, email confirmation, login, password recovery
and logout, with a separate local workspace for each account. Use Supabase Auth
directly from the desktop through a public-client adapter. Preserve the existing
glass styling and responsive Qt/asyncio design.

Admin blocking, paid entitlements, cloud document sync, social login and legacy
data import are separate tasks. Login alone does not make a sellable, remotely
revocable product. The existing shared-billing relay keeps its own approval and
budget rules throughout this phase.

## What the discussion changed

Claude received requirements and repository paths without Codex's proposal for
round one. Codex investigated the repository and current primary documentation.
Codex's initial thinking preceded reading Claude's output, but its written
position was saved afterward; there are not two independently timestamped
proposal artifacts. Round two explicitly challenged both positions with evidence.

| Question | Initial alternatives | Resolution and evidence |
| --- | --- | --- |
| Auth integration | Claude proposed a small custom httpx adapter; Codex preferred the official auth-only SDK | Prefer `supabase-auth`, conditional on transport, storage, refresh and packaging tests. Its standalone package does not depend on PostgREST, Storage or Realtime. Keep an application protocol so a demonstrated SDK incompatibility can be addressed without changing UI contracts. |
| Local isolation | Claude initially proposed one data-root change and shared provider keys | Reject that shortcut. Settings independently resolve their path; credentials, QSettings and callbacks also need explicit account scope. |
| Offline operation | Claude initially suggested seven days; Codex initially favored locking on failed revalidation | No seven-day allowance. Require online startup verification. During an already verified session, retry transient failures until the current access token expires. Do not interrupt an interview for one failed account-service request. |
| Logout | Rebuild in-process or restart | Revoke local write authority immediately, cancel work, then fully restart into the login gate. Existing Qt lifetime failures make in-process switching a larger first implementation. |
| Legacy data | Guest access and an immediate import prompt versus leaving existing data unassigned | No guest bypass in customer mode and no automatic adoption. Preserve old files and global secrets unchanged; design explicit migration separately. |
| Email delivery | SMTP only for production versus a prerequisite for hosted OTP development | Current new Free projects need custom SMTP to edit templates. This is already a hosted development prerequisite for the chosen code-entry flow. |

Agreement is not validation. No live accounts, email delivery, SDK execution or
packaged login were tested in these discussion rounds.

## Authentication and recovery

Use embedded PySide screens for signup/login and entry of email verification
codes. Customize confirmation and recovery templates to display `{{ .Token }}`.
Use `verify_otp` type `email` for signup confirmation and `recovery` for reset;
the old `signup` verification type is deprecated. Validate code length against
deployment configuration; do not bake an assumed six-digit length into the UI.

A recovery session can only change the password. It never creates the workspace
or activates provider calls. After successful reset, clear recovery credentials
and require normal login. Handle resend throttling, expired codes and generic
signup/recovery responses without exposing whether another person's account exists.

The client contains the project URL and publishable key only. Never package a
secret/service-role key, database password, developer `.env` or management token.
No desktop admin APIs. Do not send passwords through the billing relay.

Wrap the official async auth client behind typed application contracts. SDK
automatic refresh must be disabled when the application owns refresh. Serialize
refresh and fence its completion with a session generation. Use memory-only SDK
storage and persist only the required remembered-login credential in Windows
Credential Manager. Remember-me is opt-in; passwords are never retained.
Incognito disables new session persistence, with its interaction with previously
remembered sessions covered explicitly by tests.

Vault errors must not fall back to plaintext. A rotation persistence failure must
produce an explicit safe state, not a silent promise of remembered login. A logout
deletion failure must never silently restore the old session on restart. Server
signout uses explicit `local` scope for this device; other devices remain signed in.
Offline logout still locks locally and reports that remote revocation could not
be confirmed. Existing JWTs can remain valid until expiry after server signout.

## Connectivity policy

- Startup: server-verified sign-in or refresh is required before constructing an
  account workspace. Transient failures show Retry and preserve vault credentials.
- Active session: refresh before expiry in the background with bounded requests
  and retry backoff. No network auth request in the per-answer path. A transient
  failure shows an account connection notice; the currently valid session continues.
- Expiry without successful refresh, or a definitive invalid/revoked session:
  lock, stop capture and answer work, fence late callbacks and preserve committed
  history. Do not let an in-flight answer continue indefinitely past the boundary.
  Distinguish auth error codes from rate limits, generic HTTP errors and outages.
- Sleep/resume: use actual token expiry and revalidate on resume; a suspended
  timer must not create an accidental extended offline session.

This bounds ordinary offline continuation by the remaining access-token lifetime,
not by an arbitrary number of days. It is not instant block detection. Later,
server-owned protected services must independently check current account/session
and entitlement status. Local UI checks and JWT signature checks alone cannot
provide commercial enforcement. Do not promise an admin block interval yet.

## Bootstrap, storage and lifecycle

Create an immutable account context from the server-verified identity, including
project/issuer namespace, user UUID and session generation. Resolve all account
stores from `accounts/<project-namespace>/<user-uuid>/` beneath the configured
application data root. Never use email as a path or trust an unvalidated local
identity to unlock files. Validate paths and prevent traversal/reparse-point escape.

Bootstrap may load only explicitly classified device configuration, safe logs,
single-instance state and authentication state. It must not instantiate current
global `Settings()` or `build_app()` against the legacy root before login.

Account scope covers settings/candidate profile, SQLite and sidecars, resumes,
practice Q&A, usage, provider catalog, provider/Speechmatics/relay credentials,
consent, onboarding and drafts. Bind delayed callbacks to immutable context.
Single-instance coordination and non-personal window defaults may remain device
scoped. Customer mode cannot inherit global legacy keys or environment provider
credentials. Development credentials remain confined to explicit development mode.

At logout, disable new work and revoke mutation authority first. Cancel audio,
answers, imports, provider tests and refresh; detach tray/hotkeys/second-launch
entry points; fence every completion before any UI or persistence mutation.
Close stores and sockets before releasing the instance lock and restarting.
If cleanup cannot complete, remain locked and do not start another workspace.

Existing data is detected without opening stores that migrate, quarantine or
purge it. Do not claim it for the first signed-in account. A later explicit import
must copy with SQLite consistency, staged verification, atomic completion and a
retryable manifest, preserving originals. Existing global secrets require separate
ownership decisions. Account deletion and full uninstall need distinct scopes.

Account directories prevent accidental mixing inside the application. They do
not encrypt files or protect them against another process under the same Windows
OS account. No claim of such protection is part of this design.

## Repository evidence

- `src/interview_assistant/__main__.py:57-86`: global settings, credentials,
  QSettings/onboarding and application construction currently happen at startup.
- `app/composition.py:101-104`: history opens and retention purge runs immediately.
- `config/user_settings.py:56-64,129`: independent implicit settings-root resolution.
- `config/keys.py:11-31`, `config/relay.py:19-33`: global credential namespaces.
- `app/composition.py:254`, `ui/provider_catalog.py:69`: settings reread callbacks.
- `ui/bg.py:17`, `ui/relay_panel.py:158`: background completions can mutate state.
- `ui/overlay.py:173`, `privacy/consent.py:7-8`: additional registry-backed state.
- `server/interview_relay/auth.py`: friend-token billing approval, not account auth.

Paths after the first entry are relative to `src/interview_assistant/`, except
the explicit `server/` path. Line references describe the reviewed working tree.

## Implementation sequence and ownership

Use one feature branch after the implementation step starts. Root owns shared
contracts, fixtures, bootstrap/lifecycle and integration. Delegate separate auth
adapter/session, scoped-storage and UI modules only after contracts and file
ownership are agreed. Claude reviews the combined design and implementation and
can perform explicitly assigned browser validation. Do not assign shared files to
multiple writers. Existing unrelated local commits must be preserved.

1. Define protocol/state machine and immutable context; add isolated fake-provider
   tests. Prove the SDK's refresh/storage/transport behavior before adopting it.
2. Implement complete account storage/credential scope and gated startup. Add the
   UI and fenced logout/restart, with no cloud migration or legacy data mutation.
3. Validate hosted confirmation/recovery after email configuration, then native
   and packaged Windows behavior. Complete independent review and report results.

## Acceptance and rollback

Required tests include invalid/unconfirmed login, duplicate signup, OTP expiry,
recovery-only sessions, rate limits, service outage, expiry during streaming,
refresh rotation/crash, vault read/write/delete failures, logout races and resume
after sleep. Account A logout followed by B login must expose none of A's data,
credentials, drafts or callback results. Test tray/hotkey/second-launch gates.

Legacy files must remain byte-identical. Test distinct issuers, custom `DATA_DIR`,
path escape, damaged/newer databases, account purge and full uninstall. Isolate
keyring, QSettings, environment and network in addition to the existing DATA_DIR
fixture. Preserve relay budget/revocation, privacy, presentation and incognito tests.

Run lint/type checks, targeted and existing regression suites, native Qt flows,
packaged dependency checks and packaged signup/recovery/logout smoke tests. Scan
artifacts for actual embedded credentials, not simply documentation words such as
`service_role`. Validate TLS and HTTP/2 dependencies in the packaged runtime.

Rollback uses a known prior build and untouched legacy data. Never automatically
fall back to guest/legacy mode after an auth error. Account directories remain
intact and must not be opened by an older incompatible schema. No merge or push
until the user gives the corresponding go-ahead after results are reported.

## Prerequisites and primary sources

The new Free dev project needs an SMTP provider and permitted sender identity for
the chosen hosted OTP flow. No provider/domain purchase or email settings change
has been made here. SDK/fake-provider work can proceed independently; do not call
hosted signup/recovery working until delivered emails are exercised. Production
email delivery, abuse controls and service availability need a separate release
check before selling the app.

- [Supabase email-template change, June 2026](https://supabase.com/changelog/46599-changes-to-email-template-customisation-on-free-tier): new Free projects need custom SMTP for template edits.
- [Custom SMTP](https://supabase.com/docs/guides/auth/auth-smtp): default delivery is restricted to organization addresses, currently two messages per hour, and is not a production service.
- [Python OTP verification](https://supabase.com/docs/reference/python/auth-verifyotp) and [email templates](https://supabase.com/docs/guides/auth/auth-email-templates): supported code verification and template variables.
- [Sessions](https://supabase.com/docs/guides/auth/sessions) and [signout](https://supabase.com/docs/guides/auth/signout): refresh rotation, expiry and scope semantics.
- [Maintained auth-only SDK](https://github.com/supabase/supabase-py/tree/main/src/auth): reviewed source commit `1ef4688677dd1d49a0c4624898ad78b1e00defaa` exposes async httpx injection, storage hooks and refresh controls. Source inspection is not a runtime proof. PyPI reported `supabase-auth` 2.33.0; pin a tested version and lock dependencies during implementation.
- [TLS cipher change](https://supabase.com/changelog/cbc-tls-ciphers-deprecated): old CBC suites are scheduled for removal no earlier than November 10, 2026. Use supported TLS without disabling verification.
