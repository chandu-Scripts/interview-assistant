# AGENTS.md — Project Instructions (stable, read first)

## Role
Act as senior architect and implementation engineer. Work autonomously within the current step: implement, run, test, detect bugs, fix, retest, and iterate until that step's acceptance criteria pass. Do not stop for normal bugs; investigate and resolve them. Ask the human only when strictly necessary (missing API keys, hardware/permission issues, ambiguous product decisions).

## Core working rule
- When the user says **"Start Step X"**, do **only** that step. Then stop.
- Do not proceed to the next step automatically. One step per day.
- Day 1 = project setup. Each later day = one step.
- Within a step: implement → run → test (use browser/computer tools when relevant) → debug → retest → stop when acceptance criteria pass.
- End each step with a short report: what was built, how it was tested, results, known gaps.

## Project goal
Production Windows desktop AI interview assistant with:
- Real-time audio capture and streaming transcription
- Router ("Jev") that classifies each question and routes it
- Fallback LLM routing when a provider/model fails or times out
- Specialist agents (e.g. coding, system design, behavioral, data/SQL)
- Model router across OpenAI, Anthropic, and Gemini
- Streaming answers
- Compact overlay UI
- Cost/token tracking
- Session history
- Privacy controls
- Presentation mode

## Environment and stack
- Windows 11, developed in VS Code
- Python backend (asyncio-based; typed; `src/` layout)
- PySide6 for UI
- Packaging: PyInstaller or Nuitka (decide in the packaging step)
- Testing: pytest (+ pytest-asyncio, pytest-qt for UI); lint/format with ruff; type check with mypy or pyright
- Secrets: API keys via environment variables or Windows Credential Manager. Never commit keys; keep `.env` git-ignored.

## Architecture (target; refine as steps land)
```
src/<app>/
  audio/        capture (WASAPI loopback + mic), VAD, chunking
  stt/          transcription providers behind one interface, streaming
  routing/      Jev router: question classification -> agent selection
  llm/          provider adapters (openai, anthropic, gemini), model router, fallback chain, streaming
  agents/       specialist agents (prompt + tools + policy per specialty)
  cost/         token/cost accounting per request, per session
  history/      local storage (SQLite), search, export, deletion
  privacy/      redaction, retention, consent, presentation mode
  ui/           PySide6 overlay, settings, history views
  config/       typed settings, defaults, profiles
tests/          unit, integration, UI, and fake-provider fixtures
scripts/        dev helpers, packaging
docs/           decisions, step reports
```
Principles: provider-agnostic interfaces, dependency injection, every external call mockable, UI never blocks on network/audio (use worker threads/asyncio bridge), graceful degradation on every failure.

## Safety boundaries (non-negotiable)
- **No covert or deceptive screen-capture bypass features.** Do not implement anything that hides the app from screen sharing, proctoring, or recording without the user's explicit action.
- Presentation/privacy mode is allowed **only** as an explicit mode the user intentionally activates, clearly indicated in the UI, and off by default. Its purpose is to avoid accidentally showing private content (e.g., hiding the overlay while presenting), not to evade monitoring.
- No stealth/anti-detection behavior (hidden process names, disguised windows, evading proctoring software).
- Audio capture only after explicit user start; show a visible recording indicator; user is responsible for consent/legal compliance in their jurisdiction.
- Data stays local by default; history is user-deletable; API keys are never logged.
- Do not run destructive commands outside the project directory; confirm before deleting or overwriting user data.

## Testing and browser/computer usage
- Every step must include automated tests where feasible, and the step is done only when tests pass.
- When relevant to the step, run the app and use the built-in browser / computer-use tools to inspect behavior, validate flows, and fix issues.
- Use fake/mocked providers and recorded audio fixtures so tests run without network or real keys; add clearly marked optional live tests.

## Code conventions
- Python 3.11+, type hints everywhere, small modules, no dead code.
- Match surrounding style; comment only non-obvious reasoning.
- Keep `pyproject.toml` as the single source for dependencies and tool config.
- Log with `logging` (structured, no secrets, no raw audio by default).

## Step roadmap (tentative; the user's "Start Step X" message is authoritative)
1. Project setup: repo layout, venv, pyproject, lint/test tooling, config, logging, hello-world PySide6 window, CI-less local checks
2. Audio capture (loopback + mic), device selection, VAD
3. Streaming transcription
4. LLM provider adapters + streaming (OpenAI, Anthropic, Gemini)
5. Model router + fallback chain
6. Jev router + specialist agents
7. Compact overlay UI + streaming answer rendering
8. Cost tracking
9. History (SQLite), search, export, deletion
10. Privacy settings + explicit presentation mode
11. Settings/onboarding, key management
12. Packaging (PyInstaller/Nuitka), installer, smoke tests
13. Hardening: performance, error handling, end-to-end tests
14. Manual-trigger answering (default) + Auto Generate toggle
15. Overlay visual pass + Hide-to-bubble + Chat relabel
16. In-overlay Q&A history stack
17. "Create Interview Session" (company/JD/resume/instructions, created before the call)
18. Session dashboard
19. Simultaneous mic + system audio capture
20. Screenshot / vision support

Steps 14-20 are a Parakeet-inspired UX redesign; see
`C:\Users\tcs91\.claude\plans\zippy-wandering-penguin.md` for the full plan, the two Parakeet
features deliberately excluded (screen-capture evasion, cross-user question sharing — both conflict
with the safety boundaries below), and the file:line references each step builds on.

## Maintenance
- Keep this file current when stable decisions change. Add nested `AGENTS.md` files only for areas that become complex (e.g., `src/<app>/llm/`, `src/<app>/audio/`).
- Record step outcomes in `docs/steps/step-XX.md`.

## Desktop appearance (v0.1.1)
- Startup uses a centered 960 × 680 logical-pixel window, reduced for smaller work areas,
  with compact session cards. Never maximize automatically at launch.
- Shared navy/cyan styling lives in `ui/theme.py`; floating surfaces use painted translucent
  glass gradients. Preserve contrast and explicit recording/presentation indicators.
- All dropdowns use `ui/controls.py`'s ChoiceComboBox: wheel input over a closed field
  scrolls the surrounding page without changing its value, even with focus. Open popup
  lists retain scrolling and explicit selection; keyboard navigation remains available.
- Native Windows sizing must be checked as well as offscreen Qt tests: wrapped text must not
  force the top-level window taller. See `docs/glass-ui.md`.
- Navigation screens (Settings, Privacy, History, Audio/transcript) are embedded in the startup
  workspace. Tray/second launch raises that workspace. Presentation minimizes it and pauses
  capture; navigation must not reopen it during presentation. See `docs/workspace-navigation.md`.
- Resume library imports accept multiple files and preserve existing names with numbered copies.
  Resume text remains local JSON; session/history storage remains SQLite.
- Fictional / Realistic Q&A is one local practice bank (`practice_qa.json`), with explicit
  fictional examples, search and editing. Incognito blocks mutations. Text questions check
  this bank before AI routing: clear matches return unchanged, labelled Saved fictional Q&A;
  uniquely strong similar matches also return directly. Ambiguous/no matches use AI without
  selection controls. Saved fictional answers
  never become candidate-profile facts or AI conversation context. See `docs/practice-qa.md`.
  Semantic paraphrase matching uses a bundled local ONNX sentence model, with in-memory
  vectors and no network at runtime. Run `scripts/download_semantic_model.py` before packaging;
  model files are git-ignored. See `docs/semantic-matching.md` for accuracy limits and tests.
  Saved-question normalization is shared across manual saves, JSON imports and matching.
  New duplicate questions are rejected/skipped without overwriting existing answers; identical
  legacy copies may resolve, conflicting answers abstain. Semantic ranking compares compatible
  environment scopes before applying the ambiguity margin; generic counts must not answer
  production-only questions. Saved text is still returned verbatim.

## Presentation / privacy mode (decided in Step 10)
- Presentation mode hides the assistant **on the user's own screen** (overlay/history/privacy windows hidden, main window
  minimized) and pauses capture + answering. It is user-activated only (button, tray, hotkey), always OFF at startup, and
  never auto-resumes capture.
- It must NOT use OS capture-exclusion (SetWindowDisplayAffinity / WDA_*) or any other way of staying on screen while being
  invisible to screen-sharing software. `tests/test_privacy.py::test_source_never_hides_windows_from_capture` enforces this and
  `scripts/live_presentation.py` verifies display affinity is 0 on the running app.
- Redaction (PII/secrets) is applied to every outbound LLM request; incognito writes nothing to history/usage log.

## Keys and settings (decided in Step 11)
- API keys live ONLY in the Windows Credential Manager (`keyring`, service "InterviewAssistant") or in environment/.env
  (which override by default). They are never written to settings.json, logs, history or the usage log.
  An explicit in-app key Save opts that provider into Credential Manager precedence using
  `prefer_saved_keys` (provider names only). Testing, status and runtime loading share that choice.
  Removing an explicitly selected saved key does not silently reactivate a .env key. Paste fields
  remain enabled in Settings/onboarding even when a .env key is currently active.
- Precedence: explicit values > environment > .env > settings.json (`%APPDATA%\InterviewAssistant`, or DATA_DIR).
  settings.json stores only values that differ from built-in defaults, so app updates can still improve defaults.
- Settings changes apply after a restart (the app offers to restart itself; relaunch happens only after hotkeys/DB are released).
- AI providers use compact glass popups for key management and Fast/Smart presets. Settings
  and Preferred model share a credential-bound account model catalog. Red means missing or
  authentication rejected, amber means unverified/expired/network/quota/permission issue,
  green means the key passed an explicit free model-list authentication check. This does not
  guarantee credits or generation access to every listed model. A 24-hour cache stores only
  model ids, timestamps and one-way key fingerprints; incognito keeps new results in memory.
  Key replacement/removal invalidates verification. New credentials require restart before
  selecting their models for answers. Popups close on presentation. See docs/provider-picker.md.
  OpenAI model dropdowns are limited to gpt-5.4-mini (recommended Fast) and gpt-5.4
  (recommended Smart), intersected with the verified account catalog. Existing configured
  values are preserved; Use recommended models fills both presets, then Save/restart applies.
  A session's explicitly selected account model need not appear in Auto's preset chains;
  its provider must be loaded. Normal fallback and accounting still apply.
  Session preferences have separate Coding (coding/SQL) and Fast (all other questions)
  selectors. Use recommended models selects OpenAI gpt-5.4 and gpt-5.4-mini respectively,
  only when both are verified and their credential is loaded. Schema 6 stores both choices;
  NULL retains legacy single-model behavior, empty string selects Auto for that role.
  The session form has a Built-in prompt toggle below AI instructions, enabled by default.
  On disables format/length/tone dropdowns and excludes their saved values from AI requests;
  Off enables and applies them. Language and AI instructions remain available in both modes.
  Features beside the toggle shows current rule titles in an expandable glass workspace panel.
  Off omits built-in style/specialist prompts and automatic fenced-block normalization;
  explicit session preferences/instructions, relevant resume context, factuality guidance,
  and UI copy buttons remain. Schema 7 persists this flag; existing sessions default to On.
- Speechmatics is an opt-in Speech provider with its key stored alongside other credentials.
  It streams VAD audio deltas every 120 ms to the EU realtime endpoint, using one socket per
  utterance and independent mic/loopback workers. Partials update the UI; only the completed
  utterance enters question aggregation. Stop/presentation cancels pending streams.
  Failures use OpenAI if configured, otherwise local Whisper, with a 10-second reconnect cooldown.
  Speechmatics usage duration is recorded with unknown dollar cost until a pricing override is set.
  See `docs/paragraph-cards-transcripts.md` for measurements and limits.
- First run shows the setup wizard (skip with SKIP_ONBOARDING=true for automation). Tests are isolated from the real
  data dir by an autouse fixture in tests/conftest.py.

## Packaging (decided in Step 12)
- PyInstaller onedir (`python scripts/build.py`) + Inno Setup installer (`python scripts/build_installer.py`); details and the
  Smart App Control / code-signing warning are in docs/packaging.md. `build.py` gates on `scripts/check_dist_deps.py` (static
  DLL check) and runs the packaged `--self-test`. Release builds must be signed (`scripts/sign.py`); never disable Smart App Control.
- CLI switches: `--version`, `--self-test [--deep] [--out F]`, `--purge-data` (uninstaller uses it). Tests must never call
  `purge()` against the real credential store / registry (inject stand-ins).

## Shared access / relay (shipped, outside the numbered roadmap)
A separate deployable (`server/`, package `interview-relay`) lets the owner share their own OpenAI
billing with friends under an approval flow: friends request access from the desktop app's
"Shared access" settings tab, the owner approves from a hosted `/admin` dashboard, and approved
friends' `fast`/`smart` tiers route through the relay's budget-enforced `/v1/chat/completions` proxy
instead of needing their own API keys. Deployed and verified end-to-end at
https://interview-assistant-relay.fly.dev. Full design, API, and redeploy steps: `server/README.md`
and `server/DEPLOY.md`.

## Hardening decisions (Step 13)
- Live transcript previews are emitted during speech at a 750 ms audio cadence. Cloud STT
  uses a separate local tiny.en (tiny for other languages) preview worker; only final segments
  are uploaded. Preview text never triggers answers and late previews cannot replace finals.
  The configured final speech model remains unchanged. See docs/paragraph-cards-transcripts.md.
- Preview models use two CPU threads each and discard queued previews when a final segment
  arrives. Dropped STT backlog items emit empty final events to retire aggregation slots.
  Answer latency includes request scheduling, saved-Q&A lookup and classification.
- Stop cancels all answer requests submitted before the click, across audio sources, including
  saved-Q&A lookup and classification. Busy state covers the whole request. Later questions
  remain usable; late results from cancelled requests are discarded.
- Desktop classification is local by default (`JEV_CLASSIFY_WITH_LLM=false`) to avoid a
  pre-answer API round trip. Mock-interview answer style, block formatting, and latency
  decisions are documented in `docs/answer-style-latency.md`.
- Built-in answer policy is defined in `agents/answer_style.py` and composed by
  `agents/specialists.py`. Keep one authoritative filler/style rule, preserve custom session
  guidance, and omit this policy when the built-in toggle is Off. No em/en dashes in spoken
  prose; coding requests use approach, annotated code, output and complete line explanations.
  Exact filler counts remain model-dependent; see `docs/spoken-answer-style.md`. Do not add
  a silent paid rewrite call or mutate saved Q&A to enforce style.
- Output-limit finishes are saved/displayed as incomplete, never complete. The live overlay's
  explicit Continue answer action preserves raw text/code and original context, uses normal
  redaction/budget checks, and bills each request separately. Partial answers do not enter Jev
  conversation memory; only a completed combined answer does. Stop/new sessions/presentation
  invalidate continuation. See `docs/answer-style-latency.md` for tests and limits.
- Logging: rotating file `%APPDATA%\InterviewAssistant\logs\app.log` (needed: the packaged exe has no console), every line passes
  a redaction filter; questions/answers are never logged. Uncaught exceptions (main/worker threads, Qt slots) are logged and shown once.
- One instance only (lock file + local socket; a second launch raises the first). Release the lock BEFORE relaunching.
- A dead capture device is detected via the stream's own liveness (never "no data for N s": a quiet loopback delivers none).
- Damaged history.db is moved to `history.db.corrupt-<time>` and replaced; a database from a NEWER app is refused, never wiped.
- History order follows when a question was ASKED (cancelled answers finish saving later than newer ones).
- Speech model: keep base.en default (measured); vocabulary hint halves technical-term errors at no cost. STT backlog >4 warns,
  >20 drops the oldest speech. Memory is bounded (transcript panel, finalized-id set, VAD).
- Qt/PySide lifetime rule: tear down sockets/servers/signal connections synchronously in release() (an access violation
  appeared in event processing otherwise).
- Never put backslash escapes inside bash-embedded Python patch scripts here (they get mangled); use the Edit/Write tools.
