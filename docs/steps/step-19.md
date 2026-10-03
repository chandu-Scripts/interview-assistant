# Step 19: Simultaneous mic + system audio capture

## What was built
- `UtteranceAggregator` now keys all in-flight bookkeeping (pending starts, buffered text) by a
  source string instead of being single-source: `speech_started(source="")`, `final(text,
  source="")`, and `poll() -> tuple[str, str] | None` (text, source). Empty string is the historical
  single-source default, so every pre-existing caller is unaffected.
- `TranscriptEvent.source: DeviceKind | None = None` (new field) and `StreamingTranscriber(...,
  source=...)` tag which device a transcript came from; `None` means "single-source install, tag
  plainly" vs. a concrete `DeviceKind` meaning "dual-source, tag as mic/loopback".
- `AssistantController` tags questions `"voice"` (unchanged) or `"voice:mic"` / `"voice:loopback"`
  and - the one real bug found and fixed during this step - now tracks in-flight-answer state
  (`_committed_gen`, `_tasks`) **per source** instead of globally. With a single global tracker, a
  question arriving on one source could spuriously cancel or silently drop the other source's
  unrelated, concurrently-answering question; two simultaneous sources are independent
  conversations and must not do that. A newer question from the *same* source still supersedes an
  older one, unchanged from today's behavior.
- `AudioPanel` gained `restrict_kind: DeviceKind | None`, filtering its device combo to one kind -
  used to build a fixed-role "Microphone" panel and a fixed-role "System audio" panel by reusing
  the existing single-source widget twice, rather than rewriting it into a multi-row widget.
- `MainWindow` optionally wires a second `mic_panel`/`mic_provider`/`StreamingTranscriber`, relayed
  to the controller via a dedicated `mic_transcript_received` signal (deliberately bypassing
  `TranscriptPanel`, which keys its display state by utterance id - sharing it with a second,
  independently-numbered transcriber would collide two unrelated utterances onto one id). Both
  panels' `recording_changed` combine into the overlay's "listening" indicator via OR, so neither
  silently overrides the other.
- `PresentationMode`'s `audio` param widened to accept a `Sequence[Stoppable]`, so activating it
  stops *all* simultaneous captures, not just one - closing a real privacy gap the single-source
  signature would otherwise leave open.
- New opt-in `Settings.dual_audio_capture: bool = False` (Settings tab checkbox), wired in
  `composition.py`: when on, the primary panel is fixed to loopback and a second fixed-mic panel +
  transcriber are built. Off by default because of the 2x local-Whisper memory cost (see
  `docs/testing.md`).

## How it was tested
- Unit: `UtteranceAggregator` cross-contamination and independent-in-flight-count tests
  (`test_app.py`), `AudioPanel.restrict_kind` filtering (`test_audio.py`).
- Controller: a dual-source merge/tag test using an injected fake clock (`test_app.py`).
- Composition: dual-audio-off wires one unrestricted panel, dual-audio-on wires two fixed-role
  panels + two transcribers, presentation mode stops both captures (`test_composition.py`, skips
  without real audio devices).
- Settings dialog round-trip (`test_config.py`).
- End-to-end: two real `AudioCapture`/`SpeechSegmenter`/`StreamingTranscriber` pipelines (mic +
  loopback, only the hardware source faked) feeding one controller, overlapping in flight, proving
  no cross-contamination through real worker threads, not just a direct aggregator unit test
  (`test_e2e.py`).
- Manual smoke test against this machine's real audio devices (not fakes): dual-role panels wired
  correctly, both captures start simultaneously, presentation mode stops both. `--self-test` also
  confirms real device enumeration (11 devices, default loopback found).
- Full suite: 528 passed, 2 skipped. `ruff check` and `mypy src/` clean.
- Stress-tested for flakiness: `tests/test_app.py` run 25x and `test_e2e.py`'s dual-source test run
  10x with no failures, after fixing two pre-existing test races unrelated to source-tagging itself
  (both were asserting on `rec.log[-1]`/a correction list immediately after a *different,
  earlier-queued* signal's delivery, instead of waiting for the specific queued cross-thread signal
  the assertion actually depends on - a latent flake in the existing suite that extensive repeated
  runs surfaced, not something Step 19's new code introduced).

## Known gaps
- The "stop" button still stops whichever source's answer is most recently active (there is only
  one visible overlay pane); it has no per-source UI concept, which is consistent with today's
  single-pane overlay and out of this step's scope.
- `dual_audio_capture` changes apply after restart, like all other settings.
