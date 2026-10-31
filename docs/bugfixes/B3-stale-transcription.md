# B3: discard transcription from a stopped capture

## Problem and approach

Non-realtime transcription kept queued finals and delivered an in-flight OpenAI or
local Whisper result after capture stopped. Leaving presentation mode could then
make an old question eligible for automatic answering. Checking only the current
recording flag fails when capture is restarted before the old result arrives.

The agreed approach fences three boundaries:

- AudioPanel callbacks retain the physical capture epoch. The UI drops old audio,
  speech-state and level events even if they were queued before stop or arrive
  after another capture starts.
- StreamingTranscriber.cancel clears queued work and invalidates running results,
  previews and errors without waiting for the recognizer. Stop cancels instead of
  draining finals. Parent and preview share a reentrant condition, preventing
  opposing lock acquisition when a preview calls its parent.
- MainWindow tags queued worker deliveries with the capture epoch. Cancel and
  callback emission are serialized before advancing the UI epoch, so already
  queued text and errors cannot regain authority after resume.

Both mic and system audio use the same wiring, including the existing realtime
worker cancellation. Stopping one source clears its aggregation slots and live
preview only; the other source and completed transcript history remain intact.
No controller, provider API, credentials or stored-data schema changes are needed.

## Validation

Synthetic tests cover delayed success/failure for finals and local previews,
queued jobs, permanent stop, stop/restart and presentation ON/OFF, both audio
sources, already-queued Qt completions, late physical-capture callbacks and a
fresh question after each cancellation. A dual-source case verifies independent
capture and preservation of completed text.

- Focused offscreen run: 87 tests passed (before the two additional source-isolation cases).
- Native Windows Qt run: 58 tests passed, including all 22 cancellation cases and
  existing Speechmatics tests. All capture/provider inputs in those runs were fake.
- Composition, account composition, hardening, end-to-end and smoke regression:
  109 passed, one real-device loopback test deliberately deselected. Cost tests:
  27 passed. These are separate runs with overlapping coverage, not a unique total.
- Ruff and mypy passed for changed source/test scope; git diff whitespace check passed.
- The broad isolated-worktree run initially lacked ignored semantic-model assets.
  Its saved-Q&A integration failure was reproduced with an explicit model-unavailable
  warning; the existing bundled assets were copied locally before the regression rerun.
- Two existing fixtures were aligned with the lifecycle contract: the composition
  preview test now explicitly starts fake capture, and the billed-STT test waits
  for its intended completed result before asking the worker to stop. Neither
  fixture uses real capture or provider requests.

## Limit and rollback

Cancellation cannot forcibly interrupt a blocking third-party recognition call.
It prevents its result from taking effect; subsequent work on that worker starts
when the call returns. No extra inference calls or rewrite requests were added.

Revert this branch's commit to restore prior behavior. There are no data
migrations or hosted configuration changes. Merge and push remain separate approvals.

## Review follow-up: failed preview warmup after cancellation

Independent review found a health-state gap: cancelling correctly suppressed the old
warmup error, but a later warmup failure stopped the preview worker without notifying
its parent. New captures kept submitting to a dead worker. Warmup failure now retains
a non-sensitive health flag independently of capture callbacks. New active speech,
partials or a final detect that flag and report the existing final-transcription-only
notice once per capture generation. Old callbacks remain suppressed, and permanent
stop remains silent. The final provider continues normally; no additional inference
or model restart is introduced.

Five new synthetic regressions block warmup, cancel, fail, then resume before/after
failure for mic and loopback, plus permanent shutdown. They verify no stale output,
no private error detail, one notice per capture, and working final transcription.
Validation: cancellation/STT/Speechmatics 82 passed; live-transcript regressions
12 passed; Ruff and mypy passed. No microphone, provider key or network was used.
