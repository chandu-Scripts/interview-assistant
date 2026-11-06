# Interview Assistant

A Windows desktop app that listens to a conversation (system audio and/or microphone), transcribes it
using a configured local or cloud speech provider, and streams a suggested answer into a small always-on-top overlay. It routes each question to a
specialist (behavioral, coding, system design, SQL, concepts, HR), calls OpenAI / Anthropic / Gemini with
automatic failover, tracks cost, keeps a searchable local history, and has explicit privacy controls.

**Current status:** [Completed work, pending tasks, branches and release gaps](docs/PROJECT_STATUS.md).
The source includes Screen-share privacy (`78f5b54`, merged and pushed); the latest installer
has not been rebuilt. The light glass redesign and improved saved-Q&A matching remain separate branches.

**Privacy controls (see AGENTS.md):** capture requires an explicit start and visible indicator.
Presentation hides locally and pauses capture/answers. The separate Screen-share privacy toggle
starts Off, requests supported Windows capture exclusion and leaves audio/answers running;
receiver-side verification is pending and exclusion is not guaranteed. Cloud STT sends audio,
configured LLMs receive request content, and explicit screenshot requests may include an image.
Customer mode also contacts the account service. Incognito is not an offline mode.

## Run from source
```
py -m venv .venv && .venv\Scripts\activate
pip install -e .[dev]            # or: pip install -r requirements.lock
python -m interview_assistant    # first run shows a setup wizard (keys, speech model, profile)
```
API keys are stored in Windows Credential Manager; personal-mode environment / `.env` values
normally override unless an explicit in-app Save selects saved-key precedence. Customer mode
uses account-scoped keys and ignores personal environment provider keys. Source development
defaults to the personal workspace; `--customer` requires account login. See
[accounts/storage](docs/PROJECT_STATUS.md#accounts-storage-and-credentials).

## Everyday commands
| What | Command |
|---|---|
| Lint + types + tests | `python scripts/check.py` |
| Coverage | `python -m pytest --cov=interview_assistant` |
| Live tests (real Whisper + real AI, opt-in) | `RUN_LIVE=1 python -m pytest tests/test_live.py -s` |
| Build the app / installer | `python scripts/build.py` / `python scripts/build_installer.py` |
| Verify a build without running it | `python scripts/check_dist_deps.py` |
| Black-box UI checks on the real app | `python scripts/live_presentation.py`, `python scripts/live_hardening.py` (`APP_EXE=` to test the exe) |
| Benchmark speech models on this PC | `python scripts/bench_stt.py` |
| Self-test / version / uninstall data | `--self-test [--deep]`, `--version`, `--purge-data` |

## Documentation
- [AGENTS.md](AGENTS.md) - architecture and working rules
- [Project status](docs/PROJECT_STATUS.md) - completed/pending work, exact branch state, known failures and release checklist
- [Documentation index](docs/README.md) - implementation notes, decisions, bug fixes and step reports
- [Packaging](docs/packaging.md) - builds, installer and code signing / Smart App Control
- [Testing](docs/testing.md) - test layers, commands and evidence limits
