# Customer account foundation: local implementation

Date: 2026-11-01. Branch: `feature/account-login`.

## Built

The customer startup path now requires a validated Supabase email/password session
before opening a workspace. Native glass forms cover signup, confirmation codes,
login, recovery, password reset and optional remembered sign-in. Logout disposes
the workspace and requests a fresh process. Recovery credentials cannot unlock it.

Each account has separate local settings, resumes, practice Q&A, SQLite history,
usage and provider credentials. Existing personal data is not adopted or migrated.
Background refresh preserves answer latency; expired or rejected sessions lock the
workspace. Queued callbacks and persistence operations cannot regain authority
after account revocation. Incognito disables remembered login.

The pinned auth-only SDK and its dependencies are included in packaging. Bundled
public configuration targets the development project. Source development retains
the personal mode; frozen builds always require customer login.

## Review and validation

The approach was discussed with the installed Claude CLI before implementation.
Separate agents owned auth/session handling, account storage and UI. Claude's
combined review identified onboarding expiry, refresh/logout races, corrupt
markers and packaged configuration issues; these were corrected and covered by
regression tests. Contracts, integration and verification remained with the root.

Tests use synthetic accounts, fake HTTP transports and isolated credentials/data.
They cover auth failures, recovery isolation, token rotation, cancellation, expiry,
account separation, path escapes, cleanup failures and revoked UI callbacks.

Native Windows verification exercised the real startup, login, workspace, logout
and restart-request lifecycle using synthetic providers. The process exited 0.
Screenshots confirmed an 840 x 660 login window and the existing 960 x 680 workspace.
A low-contrast primary button found during inspection was corrected and rechecked.
No real microphone, provider account or credentials were used in this smoke run.

A full-suite failure was traced to an existing history test leaving its standalone
SessionDialog visible. The test now registers that dialog for fixture cleanup;
the strict single-window assertion remains unchanged.

Final results:

- Full regression suite: 1,190 passed, 2 skipped in 293.28 seconds. The additional
  wall-clock rollback regression was added after that run's collection and passed
  separately; no source code changed after the full run started.
- Ruff checks passed for `src`, `tests` and `scripts`; new account modules/tests
  passed formatting checks. Mypy passed across 111 source files. `pip check` and
  `git diff --check` passed.
- Native Windows startup/login/workspace/logout smoke passed with process exit 0.
- PyInstaller scratch build completed. Static dependency checking scanned 188
  binaries and found no missing required DLLs.
- Packaged `--self-test --deep` exited 0, with all 12 checks passing, including the
  auth module imports, Qt, SQLite, TLS, credential backend and speech dependencies.
  TLS probes used no API credentials; this is not hosted Supabase OTP acceptance.
- The bundle contains the expected public auth configuration. No `.env`,
  `settings.json`, `history.db` or `practice_qa.json` files were found in it. This
  file-inventory check is not a claim of a comprehensive binary security audit.

Local evidence is under ignored `tmp/`: `account-full-tests-accepted.log`,
`account-native-result.json`, `account-login-native.png`,
`account-workspace-native.png`, and `account-package-selftest.json`. The unsigned
scratch package is `tmp/account-package/InterviewAssistant`; it was not installed,
signed, uploaded or published.

## Hosted verification and remaining work

Read-only checks confirmed the development Auth endpoint responds, signup and email
confirmation are enabled, OTP length is eight digits, and custom SMTP is absent.
No hosted users were created, emails sent or project settings changed.

Delivered signup/recovery codes and real hosted login/logout remain unverified.
The selected OTP flow needs a permitted SMTP sender and email-template setup.
Domain/email-provider selection is the pending user input. Credentials must be
entered in provider settings, never in chat or desktop public configuration.

This step does not add paid entitlements, admin block/unblock, cloud data sync or
production deployment. Account login does not grant relay access. The current
installed app and website remain unchanged. No merge or push was performed.

Do not publish this build until hosted email acceptance and normal release checks
pass. See [implementation notes](../account-login.md) and
[architecture decision](../decisions/001-account-login-architecture.md).
