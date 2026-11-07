# Project status and handoff

Updated: 7 November 2026. Source baseline: `172bd33` on `main` (blue glass light/dark themes),
pushed to `origin/main` and remote hash verified. This is a repository/evidence review, not
a fresh audit of hosted services. A full-suite run on this baseline is recorded below.

Use this file for **what exists now and what remains**. [AGENTS.md](../AGENTS.md)
contains working rules; [the documentation index](README.md) links implementation
details. Dated reports retain their original results and are not current release claims.

## Status meanings

- **Main**: code is integrated in the source repository. It does not mean the
  installed desktop app, download website or hosted service was updated.
- **Branch only**: local implementation or preparation exists but is excluded from main.
- **Verified**: only the named test scope passed. Mock, native API, hosted development
  and actual receiver tests are different evidence.
- **Pending**: implementation, product choice, acceptance or deployment still remains.

## Current summary

| Area | Current state | Next action |
|---|---|---|
| Screen-share privacy | Main, merged and pushed at `78f5b54` | Receiver-side sharing acceptance; installer update separately |
| Blue glass light/dark themes (Orbit look) | Main, merged and pushed at `172bd33` (built on `67db178`) | Check reduced-transparency/high-contrast previews of both palettes and the screen-share indicator on the new overlay; installer update separately |
| Saved-answer equivalence | Branch `feature/saved-answer-equivalence`, `fe33293`; not release-ready | Improve paraphrase coverage without returning merely related answers |
| Existing desktop functionality | Main; now styled with the light glass theme | Keep regression coverage when integrating changes |
| Customer login/access/admin | Source foundations in main; development acceptance recorded | Production configuration, owner provisioning, email acceptance and admin hosting |
| Cloud backup/local import | Branch-only preparation | Review, approve integration and complete hosted acceptance |
| Payments | Planning only | Decide app pricing, merchant, currency and terms before implementation |
| Published app | No installer rebuilt for the latest privacy feature | Build, sign, smoke-test, approve release and publish |

## Implemented in main

| Capability | What is implemented | Evidence / important boundary |
|---|---|---|
| Audio | Explicit mic and WASAPI loopback capture; independent dual-source pipelines; recording indicators | [Step 19](steps/step-19.md); no automatic capture at launch |
| Transcription | Local Whisper, OpenAI final transcription, optional Speechmatics streaming; partial display and fallback/error categories | [Transcript behavior](paragraph-cards-transcripts.md); latency depends on hardware/provider/network |
| Audio lifecycle | Capture epochs reject stale callbacks/previews; bounded queues and backlog retirement | [B3](bugfixes/B3-stale-transcription.md) |
| Question routing | Local classification by default; specialist routing and model/provider fallback | [Step 6](steps/step-06.md), [latency](answer-style-latency.md) |
| Answer lifecycle | Streaming, Stop/cancellation, incomplete-result state and explicit Continue | [B2](bugfixes/B2-stream-completion.md); no partial answers added as complete conversation memory |
| Answer style | Main-answer-first paragraphs, copy controls, coding approach/code/output/line explanations; optional natural connectors | [Current spoken style](spoken-answer-style.md); output quality still requires human review |
| Built-in toggle | On disables format/length/tone preferences; Off enables them; custom instructions and language remain available | [Prompt toggle](builtin-prompt-toggle.md); custom instructions do not guarantee obedience |
| Answer typography | Settings Appearance and live Font toolbar: family, weight and size; code remains regular Consolas | [Typography](answer-typography.md) |
| Sessions/dashboard | Medium centered workspace, session wizard, details/resume/instructions, filters and review | [Step 17](steps/step-17.md), [Step 18](steps/step-18.md) |
| Overlay | Toolbar, transcript, paragraph cards, history navigation, chat, bubble/collapse, opacity and explicit screenshot action | [Step 15](steps/step-15.md), [Step 20](steps/step-20.md) |
| Resume library | Multiple uploads, collision-safe names, deletion, DOCX tables/headers/footers, background extraction and private session attachments | [Workspace](workspace-navigation.md); incognito blocks library persistence |
| Practice bank | One Fictional / Realistic bank; expandable questions, compulsory answers, manual entry and JSON import | [Practice Q&A](practice-qa.md); saved fictional text never becomes candidate facts |
| Existing saved matching | Normalization, direction/scope checks, local semantic lookup, verbatim saved answers | [B1](bugfixes/B1-saved-answer-direction.md); related-question false matches remain the open problem below |
| Provider settings | Credential management, free authentication check, shared model catalog, search/refresh and split Coding/Fast selectors | [Provider picker](provider-picker.md); authentication does not prove credit or generation access |
| Model choices | OpenAI picker: `gpt-6-astra`, `gpt-6.1-sol`, `gpt-6.1-terra`, `gpt-6-luna`, intersected with verified IDs; other providers retain account lists | Terra/Sol are recommendation choices, not a claim that all runtime defaults were migrated |
| History/cost | Local SQLite sessions/transcripts, search/export/delete and cost estimates | [Step 8](steps/step-08.md), [Step 9](steps/step-09.md), [B4](bugfixes/B4-history-search-actions.md) |
| Error rendering | Pending answer refresh no longer erases an existing warning | [B7](bugfixes/B7-overlay-error-render.md) |
| Local privacy | Incognito, outbound redaction, data controls; explicit Presentation hides locally and pauses capture/answering | [Step 10](steps/step-10.md), [AGENTS rules](../AGENTS.md) |
| Screen-share privacy | Separate session-only toggle; local app stays visible; requests supported Windows capture exclusion; failures report Unavailable | [Decision](decisions/screen-share-privacy.md), [validation](screen-share-privacy-validation.md) |
| Keys/cleanup | Windows Credential Manager and effective precedence; removal/cleanup failures do not falsely claim success | [B6](bugfixes/B6-credential-removal.md) |
| Customer accounts | Email/password/OTP flows, account-scoped storage, opt-in remember-me, serialized refresh, logout/revocation fences | [Account login](account-login.md); hosted email acceptance remains separate |
| Customer access | Server-owned grants/block state, bounded leases, protected RPC authorization and PT409 conflict handling | [Entitlements](customer-entitlements.md); default development config remains auth-only |
| Customer admin | Admin website source with search, block/unblock, grants and audit; isolated live development tests recorded | [Admin](customer-admin.md); website itself remains undeployed |
| Personal relay | Separate friends approval/budget proxy; concurrent reservations and cleanup hardened | [Server](../server/README.md), [B5](bugfixes/B5-relay-budget.md); customers use their own provider keys |
| Packaging/hardening | PyInstaller/Inno tooling, dependency/self-tests, single instance, rotating redacted logs, corrupt DB handling | [Packaging](packaging.md); tooling is not a newly signed release |

### Current prompt versus older requests

`src/interview_assistant/agents/answer_style.py`, composed by `specialists.py`, is the
current source of truth. Main has optional conversational markers with no filler quota,
evidence-based personal facts, and scoped project-overview routing. The full alternative
Data Engineer format-lock experiment is on `feature/data-engineer-format-lock`
(`812cad9`), **not merged**. Do not tell users that entire pasted prompt is active in main.
Old GPT comparison/pricing notes are dated experiments, not current model recommendations.

## Latest screen-share feature: delivered scope

- Branch `feature/screen-share-privacy`: implementation `cb6ac53`, evidence `3ecd990`.
  Approved merge `78f5b54`, followed by separately approved GitHub push.
- Enable under **Privacy > Screen-share privacy (supported Windows capture only)**.
  Starts Off on every launch and does not persist an enabled setting.
- Uses a single native adapter on supported Windows, own-process root windows,
  readback, rollback/reset retry, future-window handling and enabled-only auditing.
- Audio/answering are not paused by this toggle. Existing Presentation still hides
  locally and pauses. App state includes an explicit local privacy indicator.
- Post-merge tests: **78 passed, 2 opt-in native cases skipped**, then **2 native
  Windows cases passed** explicitly. No merge conflicts.
- A detached combined glass/privacy review passed **94 tests, 2 skips**, then **2
  native tests**. This was not a merge of the glass branch into main.
- **Unverified:** receiver-side Zoom/Teams/Meet/recording behavior. API readback is
  not receiver proof or guaranteed exclusion. Native popups can expose initial
  frames before the 500 ms audit. No zero-frame guarantee.
- Source push did not rebuild, install or publish an executable. An older installed
  app will not gain the toggle merely because GitHub changed.

## Open branches and their disposition

These heads were checked with Git ancestry on this update date, not inferred from
branch names. Retained merged branches do not imply unfinished work.

| Branch | Head | State / remaining work |
|---|---|---|
| `feature/saved-answer-equivalence` | `fe33293` | Conservative matching and local verifier candidate; coverage gate fails; do not merge/release as ready |
| `feature/data-engineer-format-lock` | `812cad9` | Alternative prompt experiment; excluded from main; reconcile with current style before any integration |
| `feature/legacy-import` | `b9768c4` | Explicit preview/export/append-only account import prepared; not integrated |
| `feature/cloud-backup-sync` | `92dd2e1` | Opt-in bounded private snapshots, not continuous sync; not integrated; hosted Storage acceptance pending |
| `feature/auth-email-acceptance` | `7230cbf` | Templates/runbook and artifact-bound evidence tooling; hosted delivery acceptance pending |
| `feature/production-release` | `e1b9887` | Signing/configuration/artifact evidence gates prepared; no production release built or published |
| `feature/payments` | `7475f9d` | Planning only; checkout/subscriptions not implemented |

Merged ancestors include account-login, customer-entitlements, customer-admin,
customer-access-integration, the six B1-B6 bug branches, B7 overlay error fix,
onboarding-readability, answer-typography, openai-model-options, four-openai-models,
session-model-refresh, screen-share-privacy and ios-glass-ui (`7c1f17c`, merged as
`67db178`). Main also includes the earlier
Parakeet-inspired workspace and resume/style fixes. Do not remerge these as new work.
`backup/before-bug-merge-20261101` remains a historical rollback reference, not a
recommended wholesale rollback of later account/schema changes.

### Glass UI evidence and limits

Claude CLI implemented the UI layers; Codex reviewed native screenshots, interaction
and regression tests. Root verification: **228 UI tests passed**; a separate
contrast/privacy rerun passed **108** (overlapping tests, not additive coverage).
Native synthetic dashboard, wizard, providers and overlay were inspected. The
incognito label was darkened to meet the 4.5:1 contrast threshold.

**Merged into main as `67db178` (7 November 2026), no conflicts, pushed.** On the
integration branch: ruff and mypy clean, and glass/review/screen-share/privacy/security/
onboarding tests gave 162 passed, 2 skipped. Native Windows previews in the default policy
were inspected against the Orbit web app's look. Full suite on merged main: **6 failed,
1689 passed, 5 skipped**; none caused by the merge (details in `docs/glass-ui.md`). One,
`test_overlay_window_flags_and_visibility_contract`, is a stale assertion that conflicts
with the authorized screen-share adapter and fails on the pre-merge baseline too; it needs
updating to allow only that adapter. **Not yet checked on the merged build:**
reduced-transparency and high-contrast previews, and the screen-share indicator when On
on the light overlay. No installer has been rebuilt, so the installed app keeps the old theme.

**Dark and blue-tinted themes (decision 006), merged as `172bd33` and pushed.** New `theme`
setting (Light blue default, Dark blue, Match Windows; restart to apply), blue-tinted panels,
and an overlay state edge. Contrast tests run for both palettes. Theme tests: 106 passed. A
quiet rerun of the app, start-screen, provider-picker, session-dialog, composition and e2e
files gave 265 passed and 2 failed; both failures also fail without this change (see
`docs/glass-ui.md`). One e2e test failed once under heavy machine load and passed on every
quiet run. Not done: "Up next" dashboard card, new-palette reduced-transparency and
high-contrast previews, screen-share indicator check on the new overlay, installer rebuild.

Qt-painted translucency is not true iOS/desktop backdrop blur. The overlay is static.
Real iPhone blur/keyboard behavior was not tested and this is a Windows desktop UI.
Evidence: `docs/decisions/ios-glass-ui.md` and `docs/glass-ui.md`.

### Saved-Q&A: known blocker, not a completed fix

Topic similarity previously allowed a saved answer for a different requested detail.
The candidate removes unsafe lexical acceptance, preserves operators/identifiers,
checks material scope/count predicates and uses a bundled bidirectional local verifier.
Uncertain cases automatically generate an AI answer; saved text stays unchanged.

| Evaluation | Correct saved reuse | Wrong saved reuse | Meaning |
|---|---:|---:|---|
| Development, 97 cases | 18/21 positives | 0/76 negatives | Development results only |
| Independent 60, after fixes | 17/20 positives | 0/40 negatives | Now regression data |
| Fresh 18, initially | 3/6 positives | 1/12 negatives | Found received-versus-acknowledged message count error |
| Same 18, after fix | 3/6 positives | 0/12 negatives | 50% coverage still fails the 80% promotion gate |
| Preserved older targets | 0/7 positives | Not applicable | Known compatibility/coverage losses |

Focused final tests: **70 passed**; earlier broader focused suite: **177 passed**.
Ruff, scoped mypy and model hashes passed. These do not establish semantic accuracy.
The branch build gate deliberately rejects the retained 18-case coverage failure.
No installer was produced. Finite zero-false-accept results are not universal guarantees.

Observed isolated fresh lookup p95: about 150 ms on development and 184 ms on the
18-case retest; cold model loading about 5 seconds. CPU contention caused deadline
fallbacks and about 560 ms p95 in another run. These exclude STT/LLM/network/UI.
Startup warmup is background; an initially empty bank followed by import can still
trigger a cold first lookup. No additional cloud-verifier call was added.

Full candidate evidence is `docs/saved-answer-equivalence-validation.md` **on the
matching branch**. Next work must improve coverage and retain negative tests,
or explicitly agree the local-accuracy versus cloud-latency/cost trade-off. Do not
silently restore unsafe matching or loosen the release gate to claim success.

## Accounts, storage and credentials

| Data | Current location / policy |
|---|---|
| Personal workspace | `%APPDATA%/InterviewAssistant` or explicit `DATA_DIR` |
| Customer workspace | Account root under `accounts/<issuer-hash>/<user-uuid>/`; no silent legacy adoption |
| Session/history/transcripts | Local SQLite; user deletion/export supported |
| Resume library and saved practice Q&A | Local JSON; private session-only attachments kept in memory |
| Settings/catalog/UI preferences | Local account-scoped files/INI; catalog caches nonsecret IDs/fingerprints |
| Provider keys | Windows Credential Manager or permitted personal-mode environment/.env; not settings/history/logs |
| Customer provider keys | Account-scoped credential store; customer mode ignores personal environment provider keys |
| Remember-me | Opt-in refresh credential in Credential Manager, access token in memory; nonsecret restore marker |
| Account identity/access | Supabase Auth and server-owned access/grant/audit state |
| Cloud resume/history backup | Not enabled by main; separate opt-in snapshot branch |

Incognito blocks history/usage/library persistence; it does not make configured
cloud AI/STT calls local. Outbound requests follow disclosure/redaction and explicit
capture/screenshot controls. OS account directories are not encryption or OS isolation.
Customers pay their own AI providers; app access pricing is a separate undecided matter.

## Hosted development evidence versus production

[Customer integration report](steps/customer-access-integration-2026-11-01.md)
records development migrations, the forward PT409 correction, **10 hosted Auth/RPC
checks**, **5 native hosted access checks**, and **4 isolated live admin browser
checks**. Synthetic fixture role/session/grant cleanup was verified; Auth fixture
users and audit evidence were intentionally retained as documented there.

This does not prove a deployed admin site, configured production project, delivered
signup/recovery email, paid plan or signed release. Default public desktop configuration
still targets development and remains auth-only unless deliberately configured otherwise.
Recorded advisor items include the existing `rls_auto_enable` warnings and disabled
leaked-password protection; reassess before release, do not claim they were fixed.
No hosted settings, migrations, users, payments or DNS were changed by this docs update.

## Pending checklist, in practical order

1. Verify screen-share privacy from an actual receiver; record app/version, full-screen
   versus window capture, Off/On/reset and native popup observations. No blanket guarantee.
2. Finish glass verification on merged main: reduced-transparency and high-contrast
   previews, the screen-share indicator on the light overlay, and the extra bottom-right
   "Start Session" button on the dashboard (confirm whether intended).
3. Improve saved-question equivalence coverage and fresh validation. Keep its branch
   excluded while its promotion gate fails. Preserve exact text, cancellation and privacy.
4. Reconcile stale model-selection test fixtures with the approved four-model picker.
   `test_composition_shares_verified_catalog_with_wizard` still expects removed
   `gpt-5.4-mini` and failed on unchanged main. Other older full-suite failures were
   reported in config/customer-BYOK/matcher areas; reproduce before assigning causes.
5. Review optional legacy import and cloud snapshot branches; authorize integration
   and verify real Storage authorization, cross-account denial, incognito and recovery.
6. Choose/configure production Supabase, owner admin identity, hosting access policy,
   sender/domain and SMTP. Domain purchase was deferred after registrar ID rejection;
   there is no confirmed owned domain or configured sender in recorded evidence.
7. Complete real signup/recovery delivery and OTP wrong/expired/replay cases, login,
   remember-me/logout, block/unblock, network failure and account isolation acceptance.
8. Deploy the admin site only with approved public configuration and hosting approval.
   Do not embed privileged keys or treat a local browser test as deployment.
9. Decide app prices, merchant, currency, refund/terms and fulfillment; then implement
   checkout, verified webhooks, reconciliation and entitlements. No billing activation yet.
10. Prepare signed package/installer, static dependency and packaged self-tests, native
    smoke, clean-laptop install/update/uninstall and security/release checks. Obtain
    release approval before uploading installer or updating the website download.
11. Document retention/deletion, backup recovery, operational monitoring and support
    processes for customers. Recheck observed Supabase advisor issues before launch.

## Verification rules and useful commands

Use [testing.md](testing.md) for commands and isolation. Historical counts overlap and
belong to specific trees; do not add them together or call the current full suite green.
Tests should use synthetic data and mock providers by default. Live credentials,
microphone/capture and hosted mutation tests are separate explicit runs.

```powershell
git status --short
git branch --merged main
git branch --no-merged main
git log -1 --oneline main
git ls-remote origin refs/heads/main
$env:PYTHONPATH = "$PWD/src"
$env:QT_QPA_PLATFORM = 'offscreen'
.venv/Scripts/python.exe -m pytest tests/test_capture_visibility.py tests/test_privacy.py tests/test_security_hygiene.py -q
```

For opt-in native API smoke use `QT_QPA_PLATFORM=windows` and
`CHANDOO_NATIVE_AFFINITY_TEST=1`, then run `tests/test_capture_visibility.py -k native_windows`.
This is not receiver-side acceptance. Never run purge/destructive helpers against real
data as a test, expose credentials in reports, or disable Smart App Control.

## Handoff and update procedure

The documentation refresh (`d204444`, `docs/project-status-20261107`) was subsequently
approved for merge and push by the user and merged into main without conflicts.
The source-code baseline above remains unchanged by these documentation-only commits.
Existing unrelated local edits and untracked research/configuration files were preserved
and are not silently included in commits. This is not an installer or website release.

After any approved merge/release, update the baseline, feature table, branch disposition,
test scope, remaining gaps and deployment state here. Keep historical reports intact
with a supersession link. Add evidence before changing Pending to Verified. Ask for
merge and push separately as specified in AGENTS.md; assess conflicts at that time.
