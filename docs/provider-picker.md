# Provider and model selection

The AI providers page now shows OpenAI, Anthropic (Claude), and Google (Gemini) cards.
Clicking a card opens a small navy/cyan glass popup with its key controls and Fast/Smart
model presets. Advanced routing chains are collapsed by default. Speechmatics retains its
separate key controls and Speech-tab selection.

Preferred model uses a glass picker: choose a verified provider, then a model. OpenAI is
limited to `gpt-5.4-mini` (recommended for Fast) and `gpt-5.4` (recommended for Smart).
Both Settings dropdowns and Preferred model use this shortlist, intersected with the
verified account's model list. Old cached catalogs receive the same filtering. Snapshots,
older families and unrelated models do not appear. The two-item OpenAI picker has no search
field; Anthropic and Gemini retain their searchable account catalogs.

Existing configured presets are preserved as editable text in Settings, without adding
them back to dropdown suggestions. **Use recommended models** fills both OpenAI presets;
Settings Save and restart apply them. It is disabled when a verified account is missing
either recommendation. Recommendation labels are separate from current preset assignments,
so they remain accurate before Save. Shared-access relay choices remain available independently
of personal API keys. Auto retains the existing routing and fallback behavior.

Shortlist checks cover cached/new catalogs, unavailable recommendations, preset persistence,
unchanged settings before Save, duplicate-free choices and the native glass popup flow.

Explicit session selections route through their configured provider even when the selected
account model is absent from Auto's Fast/Smart chains. The catalog and credential checks still
run at session creation; the provider checks generation access. Failure retains normal
fallback, cancellation and attempt accounting. Previously the router incorrectly treated
Auto's chains as a model allowlist and the UI reported all routing errors as a missing key.
Missing-provider errors now direct users to in-app Settings and restart, while a provider
constraint mismatch directs users to reselect the model. Regression checks cover selection
through the glass picker and session form into Jev, both recommended models, Fast/Smart
questions, an unavailable provider and model-access failure with fallback.

All app dropdowns share `ChoiceComboBox`, including Preferred model, editable provider
presets, session preferences, onboarding, audio devices and overlay categories. Mouse wheel
input over the field never changes its value, including after focus. It forwards wheel
input to the surrounding scroll viewport; the open popup list retains scrolling and click
selection. Keyboard navigation and editable text remain available. Regression checks cover
both wheel directions, pixel deltas, focused/unfocused fields, editable fields, page scrolling,
open-list selection and the custom Preferred model selector.

## Coding and Fast session preferences

The session wizard has separate Coding and Fast selectors using the same verified account
catalog and startup credential checks. Coding handles the Coding and Data/SQL categories;
Fast handles all other answerable categories, including system design and behavioral.
Manual specialist correction and continuation use the corresponding category's model.
Auto in Coding uses the Smart fallback chain; Auto in Fast uses the Fast chain.

Use recommended models selects `openai:gpt-5.4` for Coding and `openai:gpt-5.4-mini` for Fast.
Both must be listed by the verified account and ready in the running app before applying
the pair. Otherwise the form explains the missing verification, models or restart. Manual
choices can use different verified providers. No extra model-selection API call is added.

Schema 6 adds nullable `coding_model_route` and `fast_model_route` columns. NULL retains
legacy single-model behavior; an empty string explicitly selects Auto for that role. The
transactional migration preserves previous sessions, notes and model overrides. Both roles
persist for ready sessions, are copied when reusing sessions, and reset on session end.
Incognito/transcript-off sessions keep the same choices only in memory. The connection
summary displays both roles; post-call questions use the Fast choice.

## Verification and limits

- Red: no effective key, or an explicit authentication rejection.
- Amber: key saved but not tested, verification expired, connection failure, rate limit,
  quota/credit limit, service error, or account permission restriction.
- Green: an explicit Test key request successfully authenticated and fetched the account's
  model list. A saved key alone never turns green. No network request happens just by opening
  Settings or the picker.

Test key uses each provider's model-list endpoint. It generates no answer and does not check
the account's credit balance, billing readiness, or generation permission for every returned
model. The popup and success message disclose these limits. Known non-chat model families
are filtered; provider-side model availability and parameter support can still change.

OpenAI uses a bearer header; Anthropic uses `x-api-key` and its version header; Gemini uses
`x-goog-api-key`, never a key in the URL. Anthropic/Gemini pagination is followed with a bounded
page count and repeat-cursor protection. Malformed responses do not verify a key. Remote
error text is never echoed because it can contain a key; safe categories are displayed instead.
401 and Google's explicit invalid-key reason are distinct from permissions, rate limits,
credit/quota codes, network failures, and server errors.

Environment/.env keys take precedence over Windows Credential Manager by default, both for the
app and the Test key action. Settings and onboarding let the user paste a replacement even
when an environment key is active. Save selects that provider's saved key using the non-secret
`prefer_saved_keys` list in settings.json. The key itself goes only to Credential Manager; the
.env file is not edited. The popup explains the source change and restart requirement.
Removing an explicitly selected saved key leaves the provider unconfigured instead of silently
reactivating the old environment key. A source-preference write failure restores the previous
credential. Existing shadowed stored keys do not take over unless the user explicitly saves.
Testing an unsaved draft does not unlock the active provider. Saving the exact tested draft can reuse
that test; changing/removing a key resets verification. Late replies for edited, removed, or
superseded keys are ignored. Callbacks safely ignore deleted Qt widgets.

Successful results are cached locally in `provider_models.json` for 24 hours, bound to a
SHA-256 fingerprint of the effective key. This file contains no raw API key, resume, question,
or answer. A failed retest removes a previous success. Incognito does not persist new cache
changes. Cache-write failures keep the current UI usable and report that the result will not
survive restart. Actual credentials remain in Credential Manager or environment/.env.

Settings and key changes still follow the app's existing restart rule. A verified key that
differs from the running app's loaded key appears in the picker with a restart explanation;
selection is blocked until restart. A removed/expired provider disappears from the choices.
The provider popups and the Preferred model picker close during presentation mode, and they
do not reopen automatically when presentation ends.

## Validation

Tests use fake keys, mocked HTTP transports and isolated temporary data. No real API keys
were modified and no paid generation request was made for this change.

- Provider checks: explicit authentication errors versus quota/permission/network failures,
  malformed responses, account-list pagination, secrets absent from messages and caches.
- UI and state: saved versus tested drafts, environment precedence, changed/deleted keys,
  late callbacks, destroyed widgets, persisted verification and expiration, incognito,
  cache/remove failures, synchronized presets, model selection, relay preservation and restart guards.
- Native Windows: provider and Preferred model popup interaction, Escape, window sizing,
  glass appearance, selection and key-state transitions. Screenshot inspected visually.
- Integration: the assembled app shares one catalog; selected routes reach Jev; presentation
  closes both popup types.

Validation on 2026-10-25: 39 provider-picker tests passed in the final full run; the initial
36 provider tests also passed on native Windows. Full suite: **898 passed, 2 skipped,
1 credential-store test deselected, 1 pre-existing session-style precedence failure**.
Ruff passed across source/tests; mypy passed across 99 source files. The first full run also
exposed a timing race in an existing continuation test (it checked the previous UI idle state
before the failure signal arrived). Its wait now requires the visible continuation-failed
state and enabled retry button. All 26 continuation tests passed on follow-up and in the final
full run; no continuation production code was changed for this work.

Paste follow-up: 165 focused config/provider/composition tests passed, plus all 8 key-paste
tests on native Windows, including real Ctrl+V (clipboard restored), saved-key precedence
after restart, unchanged .env files, no secrets in JSON, removal without silent fallback,
preference-save rollback, onboarding and unchanged default precedence. Ruff and mypy passed.

The first follow-up test process displayed a Windows memory-read error after the pytest
summary. A minimal reproduction isolated it to restoring a Python-owned `QMimeData` object
in the test's clipboard fixture under Qt's offscreen plugin (3/3 exits were `0xC0000005`).
The app's copy buttons already use Qt-owned plain text. The fixture now restores plain text
with `setText` and skips replacing a user's non-text clipboard. The fixed reproduction exited
normally 3/3 times, and the native paste suite exited normally with all 8 tests passing.

This change updates the source-run Glass Preview. It does not rebuild or publish the website
installer. The full-suite session-style precedence assertion was already failing before this
work and is unrelated to provider selection.

## Official references checked

- [OpenAI error codes](https://developers.openai.com/api/docs/guides/error-codes)
- [Anthropic model listing](https://platform.claude.com/docs/en/api/models/list)
- [Google model listing](https://ai.google.dev/api/models#method:-models.list)
