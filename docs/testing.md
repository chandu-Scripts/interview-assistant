# Testing

`python scripts/check.py` runs ruff, mypy and the whole suite. Counts, duration and coverage
depend on the checked-out revision; the former ~400-test/40-second reference is obsolete.
See [current project status](PROJECT_STATUS.md) for verified scope and known failures.
The latest privacy merge passed 78 targeted tests and 2 explicit native Windows tests;
this is not a claim that the entire current suite passes.
Tests never touch your real data: `tests/conftest.py` points `DATA_DIR` at a temp folder for every test, and tests
that could delete stored keys or UI state (uninstall purge) inject stand-ins.

| Layer | Files | What it proves |
|---|---|---|
| Unit | `test_audio`, `test_stt`, `test_llm`, `test_router`, `test_jev`, `test_cost`, `test_history`, `test_privacy`, `test_config` | each module's behaviour, error mapping, edge cases (mocked network, scripted providers, fake clock) |
| Composition | `test_composition` | the whole app assembled in-process: every component connected, settings reach the components |
| End-to-end (silent) | `test_e2e` | real pipeline over synthetic audio: speech -> VAD -> transcript -> question merging -> router -> overlay + history + cost; failover, outages, interruption, privacy, presentation mode, budgets, device loss, 25-question session |
| Hardening | `test_hardening` | log redaction, crash handlers, single instance (real second process), audio-device watchdog, corrupted-database recovery, bounded memory (30 min soak), STT backlog |
| Packaging | `test_packaging` | CLI switches, self-test, purge, spec/installer content, dependency checker |
| Security hygiene | `test_security_hygiene` | prohibited execution/network patterns, hard-coded keys and secret persistence; capture-affinity calls restricted to the explicitly authorized native adapter |
| Live (opt-in) | `test_live` | real Whisper + real AI provider fed with synthesized speech (no speakers) |

Black-box checks on the running app (Windows, real windows and hotkeys): `scripts/live_presentation.py` and
`scripts/live_hardening.py`; set `APP_EXE` to run them against the packaged exe. They need a desktop session and
(for the exe) a correctly signed/allowed build. Never disable Smart App Control.
Run `live_presentation.py` with Screen-share privacy Off when checking zero display affinity.

Timing-based tests need measured tolerances. Investigate contention/races before changing
assertions; do not weaken production deadlines or accuracy gates just to obtain a pass.
Native display-affinity tests opt in with `CHANDOO_NATIVE_AFFINITY_TEST=1` and
`QT_QPA_PLATFORM=windows`; they do not prove receiver-side capture exclusion.

## Performance reference (this PC: 8 cores, CPU only)
`python scripts/bench_stt.py` - 29 s of clean synthetic speech:
| model | real-time factor | word errors (no hint -> with vocabulary hint) |
|---|---|---|
| tiny.en | 0.10 | 2.4% -> 1.2% |
| **base.en (default)** | 0.19 | 2.4% -> 1.2% |
| small.en | 0.55 | 0.0% -> 0.0% |
A 6 s question finishes transcribing ~1.1 s after speech ends with base.en (~3.3 s with small.en). Thread count (4 vs 8)
made no difference. The overlay chip tooltip shows where the time went for every answer.

## Dual audio capture (Step 19)
`Settings.dual_audio_capture` runs mic and system-audio (loopback) as two fully independent
pipelines: separate `AudioCapture`/`SpeechSegmenter` instances (already stateless per-instance, no
change needed there), two `StreamingTranscriber`s, and - when `stt_provider="local"` - two loaded
Whisper models instead of one. This is accepted as a straightforward **2x local-Whisper memory**
cost for the feature and is on by default. Cloud final transcription avoids those final
Whisper models, but the configured live preview/fallback path can still load local models;
do not assume cloud speech has zero local CPU/memory cost. `UtteranceAggregator` keys its in-flight bookkeeping by
source string (`""`, `"mic"`, `"loopback"`) so a mic utterance and a loopback utterance in flight at
the same time can never merge into each other's text, and `AssistantController` keys its
in-flight-answer bookkeeping (`_committed_gen`, `_tasks`) by that same source string so a question
from one source can never cancel or drop the other source's unrelated, concurrently-answering
question - only a newer question from the *same* source still supersedes an older one.
