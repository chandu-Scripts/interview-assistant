# Testing

`python scripts/check.py` runs ruff, mypy and the whole suite (about 40 s, ~400 tests, ~95% line coverage).
Tests never touch your real data: `tests/conftest.py` points `DATA_DIR` at a temp folder for every test, and tests
that could delete stored keys or UI state (uninstall purge) inject stand-ins.

| Layer | Files | What it proves |
|---|---|---|
| Unit | `test_audio`, `test_stt`, `test_llm`, `test_router`, `test_jev`, `test_cost`, `test_history`, `test_privacy`, `test_config` | each module's behaviour, error mapping, edge cases (mocked network, scripted providers, fake clock) |
| Composition | `test_composition` | the whole app assembled in-process: every component connected, settings reach the components |
| End-to-end (silent) | `test_e2e` | real pipeline over synthetic audio: speech -> VAD -> transcript -> question merging -> router -> overlay + history + cost; failover, outages, interruption, privacy, presentation mode, budgets, device loss, 25-question session |
| Hardening | `test_hardening` | log redaction, crash handlers, single instance (real second process), audio-device watchdog, corrupted-database recovery, bounded memory (30 min soak), STT backlog |
| Packaging | `test_packaging` | CLI switches, self-test, purge, spec/installer content, dependency checker |
| Security hygiene | `test_security_hygiene` | no shell/eval/pickle/plain HTTP/`verify=False`/capture-evasion APIs, no hard-coded keys, https-only endpoints, secrets never in settings.json |
| Live (opt-in) | `test_live` | real Whisper + real AI provider fed with synthesized speech (no speakers) |

Black-box checks on the running app (Windows, real windows and hotkeys): `scripts/live_presentation.py` and
`scripts/live_hardening.py`; set `APP_EXE` to run them against the packaged exe. They need a desktop session and
(for the exe) a PC whose Smart App Control allows the unsigned build.

Timing-based tests use generous margins; if one flakes under load, widen the margin rather than retrying.

## Performance reference (this PC: 8 cores, CPU only)
`python scripts/bench_stt.py` - 29 s of clean synthetic speech:
| model | real-time factor | word errors (no hint -> with vocabulary hint) |
|---|---|---|
| tiny.en | 0.10 | 2.4% -> 1.2% |
| **base.en (default)** | 0.19 | 2.4% -> 1.2% |
| small.en | 0.55 | 0.0% -> 0.0% |
A 6 s question finishes transcribing ~1.1 s after speech ends with base.en (~3.3 s with small.en). Thread count (4 vs 8)
made no difference. The overlay chip tooltip shows where the time went for every answer.
