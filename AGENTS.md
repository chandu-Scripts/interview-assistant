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

## Maintenance
- Keep this file current when stable decisions change. Add nested `AGENTS.md` files only for areas that become complex (e.g., `src/<app>/llm/`, `src/<app>/audio/`).
- Record step outcomes in `docs/steps/step-XX.md`.

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
  (which override). They are never written to settings.json, logs, history or the usage log.
- Precedence: explicit values > environment > .env > settings.json (`%APPDATA%\InterviewAssistant`, or DATA_DIR).
  settings.json stores only values that differ from built-in defaults, so app updates can still improve defaults.
- Settings changes apply after a restart (the app offers to restart itself; relaunch happens only after hotkeys/DB are released).
- First run shows the setup wizard (skip with SKIP_ONBOARDING=true for automation). Tests are isolated from the real
  data dir by an autouse fixture in tests/conftest.py.

## Packaging (decided in Step 12)
- PyInstaller onedir (`python scripts/build.py`) + Inno Setup installer (`python scripts/build_installer.py`); details and the
  Smart App Control / code-signing warning are in docs/packaging.md. `build.py` gates on `scripts/check_dist_deps.py` (static
  DLL check) and runs the packaged `--self-test`. Release builds must be signed (`scripts/sign.py`); never disable Smart App Control.
- CLI switches: `--version`, `--self-test [--deep] [--out F]`, `--purge-data` (uninstaller uses it). Tests must never call
  `purge()` against the real credential store / registry (inject stand-ins).

## Hardening decisions (Step 13)
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
