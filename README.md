# Interview Assistant

A Windows desktop app that listens to a conversation (system audio and/or microphone), transcribes it
locally, and streams a suggested answer into a small always-on-top overlay. It routes each question to a
specialist (behavioral, coding, system design, SQL, concepts, HR), calls OpenAI / Anthropic / Gemini with
automatic failover, tracks cost, keeps a searchable local history, and has explicit privacy controls.

**Safety rules (see AGENTS.md):** capture only starts when you press *Start capture* and a red RECORDING
indicator is shown; presentation mode hides the assistant on *your own* screen and is never used to evade
screen sharing or monitoring; nothing leaves the PC except the text sent to the AI providers you configure.

## Run from source
```
py -m venv .venv && .venv\Scripts\activate
pip install -e .[dev]            # or: pip install -r requirements.lock
python -m interview_assistant    # first run shows a setup wizard (keys, speech model, profile)
```
API keys are stored in the Windows Credential Manager (or in environment / `.env`, which override).
Everything else is in **Settings** (or `%APPDATA%\InterviewAssistant\settings.json`).

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
- `AGENTS.md` - architecture, rules and decisions (start here)
- `docs/steps/step-01.md ... step-13.md` - what was built in each step, how it was tested, known gaps
- `docs/packaging.md` - building, the installer, **code signing / Smart App Control**
- `docs/testing.md` - the test layers and how to run them
