# Documentation index

Updated: 7 November 2026.

Start with [Project status and pending work](PROJECT_STATUS.md).
Repository rules: [AGENTS.md](../AGENTS.md). Source setup: [README](../README.md).

Dated reports record their original validation; they do not automatically describe
current main, a hosted deployment or the installed app. Branch-only reports are
identified in the status document and remain on their respective branches.

## Feature guides and reports

- [Customer account foundation](account-login.md)
- [Faster answer startup and mock-interview answer style](answer-style-latency.md)
- [Answer typography](answer-typography.md)
- [Built-in prompt toggle](builtin-prompt-toggle.md)
- [Customer access hosted acceptance runner](customer-access-acceptance.md)
- [Customer admin website (F1)](customer-admin.md)
- [Customer access foundation (F2)](customer-entitlements.md)
- [Desktop glass refresh — v0.1.1](glass-ui.md)
- [GPT-5.4 mini option and live test](gpt54-mini.md)
- [GPT-5.6 Luna live smoke test](gpt56-luna-test.md)
- [Additional OpenAI choices (2026-11-06)](openai-model-options.md)
- [Packaging and installing](packaging.md)
- [Paragraph copying and transcript visibility](paragraph-cards-transcripts.md)
- [Fictional / Realistic Q&A](practice-qa.md)
- [Project status and handoff](PROJECT_STATUS.md)
- [Provider and model selection](provider-picker.md)
- [Screen-share privacy validation](screen-share-privacy-validation.md)
- [Local semantic saved-answer matching](semantic-matching.md)
- [Conversational answer prompt](spoken-answer-style.md)
- [Testing](testing.md)
- [Vocabulary-hint echo suppression](transcription-prompt-echo.md)
- [Feature branch work report, 1 November 2026](work-report-2026-11-01.md)
- [Single-window workspace and batch resume import](workspace-navigation.md)

## Decisions

- [Customer account login architecture](decisions/001-account-login-architecture.md)
- [Isolated bug fixes and production preparation](decisions/002-isolated-bugs-and-production-work.md)
- [Customers use their own AI provider keys](decisions/003-customer-owned-api-keys.md)
- [Customer access, admin and BYOK integration](decisions/004-customer-access-integration.md)
- [Natural answers and project overviews](decisions/005-natural-answers-project-overview.md)
- [Explicit screen-share privacy](decisions/screen-share-privacy.md)

## Bug-fix evidence

- [B1: preserve direction when selecting a saved answer](bugfixes/B1-saved-answer-direction.md)
- [B2: require explicit stream completion](bugfixes/B2-stream-completion.md)
- [B3: discard transcription from a stopped capture](bugfixes/B3-stale-transcription.md)
- [History search actions](bugfixes/B4-history-search-actions.md)
- [B5: atomic relay budget reservations](bugfixes/B5-relay-budget.md)
- [Verified credential removal](bugfixes/B6-credential-removal.md)
- [Keep error messages visible after a queued answer refresh](bugfixes/B7-overlay-error-render.md)

## Step reports

- [Customer account foundation: local implementation](steps/account-login-foundation.md)
- [Customer access, admin and customer-owned keys](steps/customer-access-integration-2026-11-01.md)
- [First-run setup readability](steps/onboarding-readability-2026-11-01.md)
- [Step 1 — Project setup](steps/step-01.md)
- [Step 2 — Audio capture](steps/step-02.md)
- [Step 3 — Streaming transcription](steps/step-03.md)
- [Step 4 — LLM provider adapters + streaming](steps/step-04.md)
- [Step 5 — Model router + fallback chain](steps/step-05.md)
- [Step 6 — Jev router + specialist agents](steps/step-06.md)
- [Step 7 — Compact overlay + end-to-end pipeline](steps/step-07.md)
- [Step 8 — Cost tracking](steps/step-08.md)
- [Step 9 — History (SQLite), search, export, deletion](steps/step-09.md)
- [Step 10 — Privacy settings + explicit presentation mode](steps/step-10.md)
- [Step 11 — Settings, onboarding and key management](steps/step-11.md)
- [Step 12 — Packaging, installer and smoke tests](steps/step-12.md)
- [Step 13 — Hardening: performance, error handling, end-to-end tests](steps/step-13.md)
- [Step 15 — Live overlay visual refresh](steps/step-15.md)
- [Step 17 — Session setup and model preference](steps/step-17.md)
- [Step 18 — Session dashboard, Parakeet UI refresh](steps/step-18.md)
- [Step 19: Simultaneous mic + system audio capture](steps/step-19.md)
- [Step 20: Screenshot / vision support](steps/step-20.md)

## Other components

- [Personal relay](../server/README.md)
- [Relay deployment](../server/DEPLOY.md)
- [Website](../website/README.md)
