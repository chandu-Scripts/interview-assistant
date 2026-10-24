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

## Speech latency follow-up — October 11, 2026

- The current development configuration uses cloud STT, manual answering and dual audio.
  No provider/key, recording preference or user settings file was changed.
- The utterance merger now measures its 500 ms quiet window from the audio segment end,
  overlapping it with transcription. Previously it always waited another 500 ms after
  transcription returned. Endpoint timestamps and latency bookkeeping are per source;
  resumed speech still holds the question while its transcription is in flight.
- Failed final transcription jobs now emit an empty final event, retiring the utterance
  so it cannot leave later questions waiting for a result that will never arrive.
- Default Jev classification timeout is now 1.0 s instead of 2.5 s. Difficult classifications
  fall back to rules sooner; explicit configured overrides remain honored. This changes
  specialist-selection latency, not the answer model's first-token latency.
- Focused pipeline suite: 212 passed. Deterministic clock tests verify overlap and continued
  same-source merging/source isolation; STT failure coverage verifies the empty final event.
  Full suite: 633 passed, 2 skipped. Ruff, mypy and diff checks pass.
- Recognition substitutions are not diagnosed: no failing audio or spoken/recognized phrase
  pair was provided. English-only recognition remains configured. No microphone capture,
  real-provider calls or end-to-end hardware speed claims were made for this follow-up.
- Source changes require restarting the source app; an installed executable needs rebuilding.
  These changes are uncommitted on main, after the earlier UI feature merge.

## Question relevance follow-up - October 11, 2026

- Report: a list-versus-tuple question received a candidate introduction. The actual
  recognized question and live provider request are unavailable, so the incident's cause
  remains unconfirmed.
- Comparison rules now recognize "difference btw", "differences between" and "b/w".
  The reported wording goes directly to Concept without a classifier network request,
  including after an HR introduction. Existing SQL/coding routing tests remain green.
- Answer prompts prioritize the latest question, treat resume text as background facts,
  and prevent session style instructions from replacing the question with an unrelated
  introduction. Conversation context remains available for follow-ups.
- Validation: 200 tests passed across Jev, controller and end-to-end suites with fake
  providers. Request inspection covers a technical question after an introduction with
  resume and first-person session instructions. Ruff and diff checks passed. Mypy was
  blocked by Windows Application Control on its DLL, including an escalated retry.
- No paid model calls or audio capture were performed; mock tests do not prove live answer
  relevance. Source restart or executable rebuild is needed to use these uncommitted changes.

## Pre-launch desktop hardening - October 24, 2026

- Presentation pause now invalidates queued classifications and cancels all concurrent answer
  tasks. Rapid unpause cannot revive old work, corrections are blocked while paused, and
  microphone/loopback filler is consistently ignored. The stop target falls back to another
  running answer when the most recent one finishes.
- Changing interview sessions cancels prior work and clears conversational context. History
  writes carry the generation captured when the question was submitted; late answers cannot
  populate a new session or recreate history after Delete all.
- Interview metadata is memory-only when history is disabled, incognito is active, or the
  session has transcript saving disabled. Private sessions never start saving mid-call.
  Resume-library mutations are blocked in incognito; selecting an existing resume does not
  update its saved last-used timestamp.
- Delete all now removes interview metadata and notes as well as turns. Retention removes
  expired, unreferenced interview metadata and preserves active-session notes. No actual user
  history or resume data was deleted during this work.
- Per-answer cost accounting uses task-local request context, including classification and
  fallback attempts. Concurrent answers and STT no longer inflate each other's displayed
  costs; unknown prices remain unknown. In-flight LLM usage retains its original cost session
  and cannot persist after an intervening privacy change or usage-log wipe. Post-call review
  also participates in request context. Hard budgets are checked before classification.
- Added the missing keyring runtime dependency (already pinned in requirements.lock), and
  stopped logging raw unparseable classifier responses.
- Startup self-test passed, including Qt, SQLite FTS5, credential backend, Whisper imports and
  audio-device enumeration. Dashboard and overlay previews were regenerated and visually
  inspected offscreen. No microphone capture or paid AI calls were made.
- Scope: desktop correctness fixes. Live speech accuracy, provider latency, signed installer
  rebuild/release and previously deferred dashboard/product additions remain separate
  validation or feature work. Hosted relay spend reservations were not changed or deployed.
  The desktop shortcut launches the updated source; changes remain uncommitted.
- Final validation: 655 passed, 2 skipped in the full offscreen desktop suite; Ruff lint,
  formatting checks, mypy (86 source files), pip dependency check and git diff checks passed.
