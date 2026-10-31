# Customer account foundation

Implemented on `feature/account-login`, following the [architecture decision](decisions/001-account-login-architecture.md).
This is an account foundation for development, not a commercial release. Admin
blocking, entitlement enforcement, payments, cloud sync and legacy import remain separate work.

## Run and configuration

Source personal mode retains existing behavior. Run the customer flow explicitly:

```powershell
.venv\Scripts\python.exe -m interview_assistant --customer
```

Packaged builds always use customer mode. `assets/auth.json` contains only the
development project's public URL and publishable key. Source runs support
`SUPABASE_URL` and `SUPABASE_PUBLISHABLE_KEY` overrides; packaged builds ignore
these overrides. A missing/malformed public configuration stays on a locked screen.
Never put service-role keys, database passwords or SMTP credentials in this file.

The login window offers email/password signup, confirmation code entry, password
recovery, normal sign-in and explicit restoration of a remembered sign-in.
Remember-me starts unchecked. Recovery goes back to normal login after changing
the password and never constructs the account workspace. Sign out closes the
current workspace and restarts into login. Server logout uses current-session
scope, so other devices retain their sessions.

## Data and lifecycle

The original personal workspace remains untouched. Customer workspaces live below
`DATA_DIR/accounts/<issuer-hash>/<user-uuid>/` (normally under APPDATA's
InterviewAssistant directory). Settings, SQLite, resumes, practice bank, usage,
provider catalog and INI preferences use this root. Provider and relay credentials
use account-specific Windows Credential Manager services. No first-user adoption
of existing personal data or API keys occurs.

Refresh credentials live in a separate project-and-user credential namespace.
Only a nonsecret restore marker is stored under `DATA_DIR/auth/<issuer-hash>/`.
Marker corruption blocks remembered restore and is repaired to an inactive state
on explicit login/cleanup. An orphan Windows credential whose UUID is lost cannot
be enumerated safely; it remains unused and is not claimed as deleted.

The immutable account context is installed once. Revocation gates settings and
credential writes, library mutation, history/usage persistence, queued worker
callbacks, tray actions, navigation and presentation changes. Logout stops capture
and generation before disposing windows/stores. A session lock during the nested
onboarding wizard is latched, revokes the context and rejects that wizard.

Refresh runs in the background, never in the answer path. Transient account-service
failure preserves credentials and retries; expiry or a definitive invalid session
locks the workspace. Monotonic elapsed time prevents a backwards wall-clock change
from extending the current offline window. Rotated tokens are retained before the
additional user validation request, so retry after a network outage uses the new
credential. These desktop checks do not enforce server-side commercial access.

## Hosted email prerequisite

Read-only checks of the development project confirmed signup enabled, email
confirmation enabled, an eight-digit OTP with 3600-second expiry, and no custom
SMTP configuration. A public-key GET of `/auth/v1/settings` returned HTTP 200 and
confirmed email auto-confirmation is off. No users were created and no emails sent.

New Free projects cannot edit email templates using Supabase's default mail
service. Configure a permitted SMTP sender before changing confirmation and
recovery templates to display `{{ .Token }}`. Do not disable email confirmation to
make the tests appear to pass. SMTP credentials belong in Supabase's server-side
settings, never the desktop config. Domain/provider selection remains a user action.

After setup, verify delivered signup codes, expired/wrong codes, login, recovery,
reset, saved-session restore and logout against the hosted project. Confirm rate
limits, abuse controls and sender deliverability before inviting customers.

## Verification

Automated coverage uses synthetic identities, isolated stores, fake credentials
and HTTP transports exercising the actual SDK. It includes refresh rotation,
network and vault failures, cross-thread cancellation, logout races, corrupt
markers, account separation, reparse-point escapes, recovery isolation and full
workspace teardown. Existing regression tests cover audio, answer routing,
privacy, resumes and billing behavior. See the
[implementation report](steps/account-login-foundation.md) for final counts.

A native Windows smoke run exercised the actual startup path with synthetic
providers: login at 840 by 660 logical pixels, workspace at 960 by 680, logout,
restart request and process exit 0. Window screenshots were inspected. It used no
real credentials, capture or network; it does not substitute for hosted OTP tests.

## Rollback and release

The existing installed app and website are unchanged. A prior build can use the
untouched personal workspace; do not merge account data back into it automatically.
Do not publish this branch before hosted email acceptance, deployment configuration
review and normal signing/release checks. Merge and push require the user's
separate go-aheads after reporting validation and actual conflict status.
