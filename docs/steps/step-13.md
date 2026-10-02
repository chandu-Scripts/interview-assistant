# Step 13 — Hardening: performance, error handling, end-to-end tests
Measured first (scripts/bench_stt.py, 8-core CPU, 29 s of speech): base.en RTF 0.19 / 2.4% word errors; tiny.en 0.10 / 2.4%;
small.en 0.55 / 0.0% (too slow live); thread count no effect; a short vocabulary prompt HALVES errors (2.4% -> 1.2%) at no speed cost and
does not hallucinate on silence/noise/tone -> added `stt_vocabulary` (editable), kept base.en default. A suspected cold-TLS latency was
measured (0.97 s vs 0.82 s warm) and rejected as noise, so no speculative connection pre-warming was added.
Resilience built: rotating redacted file log + crash handlers (the windowed exe previously lost all logs); single-instance guard
(lock file + local socket, second launch raises the first, released before relaunch); audio-device watchdog (stream liveness, no false
alarms on quiet loopback); corrupted-history recovery (quick_check, quarantine, user notice; newer-schema DBs refused); bounded memory
(transcript panel, finalized-id set), STT backlog warning + drop-oldest cap; latency breakdown per answer (speech end -> question ->
first word -> total, in the chip tooltip); composition root split out of __main__ (app/composition.py) so the whole wiring is testable.
Bugs found by the new tests and fixed: (1) history listed an interrupted answer AFTER the newer one (turns now stamped with ask time);
(2) intermittent access violation in Qt teardown from SingleInstance sockets outliving release() (~1 in 4 runs -> 0 in 8+);
(3) my own guard patterns / tests had false positives and a load-sensitive race (timing margins widened).
Tests: 397 pass, ~95% coverage (was 92%): test_e2e (16 silent end-to-end scenarios), test_hardening (30), test_composition (11),
test_security_hygiene (17), test_live (opt-in, real Whisper + real LLM from synthesized speech: exact transcript, gpt-4o-mini answer,
$0.00009, 5 s speech-to-saved-answer), plus a 30-minute VAD soak (memory flat). pip-audit: only pip itself had advisories (upgraded);
runtime dependencies clean; requirements.lock pins 86 packages.
Black-box on the REAL app, source AND packaged exe: 11/11 hardening checks (second launch exits in ~1.4 s, no duplicate windows, log
file + startup lines + no secrets, relaunch after exit, corrupted history.db tolerated with user notice and file kept) and the
presentation-mode/hotkey/capturable check. Rebuilt package: 313 MB, 241 files; installer 86 MB; packaged self-test OK.
Known gaps: no code signing (Smart App Control blocks unsigned builds on other PCs); speech is CPU-only (no CUDA bundled) and
small.en/medium.en are too slow for live use on this PC; real-world accuracy with noisy/accented audio is unmeasured (benchmark uses
clean TTS); Anthropic path still untested live (no key); no crash-report upload by design (logs stay local); no CI pipeline file
(nothing to run it on here); interactive uninstall/purge still not exercised end to end; DB unencrypted at rest (BitLocker/incognito).
