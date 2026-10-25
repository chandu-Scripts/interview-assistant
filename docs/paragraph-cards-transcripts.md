# Paragraph copying and transcript visibility

Implemented October 25, 2026 in the source preview.

## Changes

- Answers render as separate paragraph cards with individual copy buttons. Code stays in one card, preserving indentation and blank lines. The existing Copy action still copies the whole answer.
- Final cloud transcription events now reach the overlay immediately, even when the provider sends no partial transcripts.
- The main transcript view includes microphone and system audio. Entries use source plus utterance ID so overlapping counters do not overwrite one another.
- Short technical questions such as “Explain ADF” and “What is CDC” are recognized without requiring transcription punctuation.
- Empty final transcripts and transcription errors show a visible audio status instead of appearing to do nothing.

## Validation

- 300 tests passed across answer rendering, routing, composition, transcription, app behavior, hardening, and end-to-end fake-provider flows.
- Ruff passed for the changed implementation/tests; mypy passed for all 93 source files.
- The isolated native Windows preview verified paragraph cards, independent clipboard copying, saved-answer lookup, and workspace navigation. Screenshot: `tmp/workspace-preview/answer-block-format.png`.

## Limits

Tests used fake providers and simulated transcript events. Live device capture and cloud recognition quality were not measured. These fixes address demonstrated display/routing failures and add diagnostics; they do not guarantee that every spoken question will be recognized. No real settings, saved questions, or resumes were changed. Quit and reopen the source preview to load the changes. The installed release and website download are unchanged.

## Live previews during speech (October 25, 2026)

Cloud recognition now uses a separate local tiny.en preview worker (multilingual tiny when
the configured language is not English). The VAD emits growing audio snapshots every 750 ms;
the preview queue coalesces them to the latest available snapshot. The existing selected
provider transcribes the final segment once, correcting the provisional text. Local-only
recognition also receives the faster snapshot cadence and retains its selected model.

The preview model warms on a background thread at startup. It may require an initial model
download on a new laptop; a missing or failed model produces one visible warning and leaves
final transcription working. No preview audio is uploaded, so repeated cloud billing is
avoided. Slow previews cannot block final cloud requests or overwrite newer/final text.
Mic and loopback have independent workers. Only final transcripts enter question aggregation;
Manual and Auto answering behavior is preserved.

Validation: 113 focused and end-to-end tests passed after final changes; 5 native Windows
UI checks passed for both sources and answering modes. The full offline regression run before
the final lightweight-model/startup checks had 789 passes, 2 skips, one credential test
deselected, and the existing unrelated session-style precedence failure. Ruff and mypy passed.

An offline generated-speech experiment used real cached Whisper models and a fake cloud final
provider. With tiny.en, the first preview arrived 2.02 seconds after feeding began, followed
by nine total previews approximately 0.6-0.9 seconds apart, and one final upload call. Separate
model loading took 2.44 seconds. Base.en produced only three previews, first at 3.19 seconds,
so tiny.en was selected for previews only. Provisional recognition contained errors that the
fake final replaced; this experiment does not establish real cloud accuracy or microphone
performance. There is no promise of instantaneous word-by-word captions. No personal audio
was captured and no paid API calls were made. Restart the source preview; installer unchanged.

## Speechmatics live transcription (October 25, 2026)

Select **Settings → Speech → Speechmatics live**, save, and restart the source preview.
The credential is stored in Windows Credential Manager under `InterviewAssistant/speechmatics`;
`SPEECHMATICS_API_KEY` in the environment/.env overrides it. Other provider defaults are unchanged.

The Speechmatics path opens an authenticated EU WebSocket when VAD detects speech following
explicit capture start. Growing snapshots are produced every 120 ms, but the worker sends only
the new PCM samples. The socket receives partials concurrently with upload. Committed transcript
segments accumulate, speculative text is replaced, and one final event is emitted when the
utterance finishes. Microphone and loopback use separate workers and source IDs. Only final
events enter the existing question aggregator; partials do not trigger answers.

The standard model uses partials and a fixed 0.7-second server max-delay setting. Session Info
messages are allowed before RecognitionStarted. Custom vocabulary is not sent: initializing
large dictionaries can add latency. Each utterance has its own connection (at most one active
per source), so connection overhead still affects the first text of each question. This is
streaming during speech, not a permanently open session. No network is opened by warmup/startup.

Stop/presentation invalidates work and cancels the socket sender. Queued speech is bounded;
overflow retires the dropped utterance. Capture stop resets that source's aggregation state,
so cancelled requests cannot leave a phantom backlog. Network/protocol failures fall back to
OpenAI when its key is configured, otherwise local Whisper. A 10-second reconnect cooldown
prevents rapid retries; fallback failures retire the utterance and show a visible error.
Fallback may be slower and local model initialization may require a download. Settings and
Privacy disclose the selected cloud provider and fallback. WebSocket debug logging is disabled
to protect Authorization headers, and raw remote exception details are never displayed.

Duration usage is tracked for Speechmatics and separately for any fallback. Speechmatics dollar
cost is deliberately unpriced rather than shown as free; use a `speechmatics:standard` audio
pricing override for the account's actual per-minute rate.

An authorized live synthetic-WAV test on the real EU endpoint returned 38 partial updates,
first text 0.924 seconds after VAD speech start, and final text 0.413 seconds after VAD end
(the local 420 ms silence detector is additional). It correctly transcribed: “How do you
optimize SQL queries when the table contains millions of rows and the query takes too long
to finish?” No fallback occurred. This is one sample, not a latency guarantee or a measurement
of the user's microphone/accent. The opt-in `scripts/test_speechmatics_live.py` reproduces the
test with a chosen synthetic 16-bit WAV and uses API credit; it never captures a microphone.

Official protocol reference: https://docs.speechmatics.com/api-ref/realtime-transcription-websocket
The installed release and website download are unchanged; this implementation is in the source preview.

Validation: the full regression run passed 812 tests, skipped 2 and excluded the real-credential
test; the existing unrelated session-style precedence assertion still fails. After adding
Manual/Auto end-to-end and composition coverage, the final focused run passed 244 tests.
Native Windows checks passed 16 cases plus the two answering-mode UI cases. Ruff and mypy
(96 source files) passed. The user's saved provider and the overriding local `.env` provider
were both changed to `speechmatics`; effective configuration was checked. Restart is required.
# Transcription status recovery

The overlay shows the actual status message instead of mapping every notice to a generic
"Transcription issue / Check audio" error. Empty final results show a no-words notice;
temporary streaming failure explains fallback and its end-of-question timing. The next
nonempty partial clears the old warning immediately. If fallback also fails, its failure
is emitted after the empty final so the useful error remains visible. Stop/cancel suppresses
late fallback errors as well as late text. Speechmatics logs only the failing stage and a
timeout/failure category, never remote exception text, credentials, audio or transcripts.

The October 25 screenshot's exact trigger could not be recovered from the old logs, which
did not record streaming failure stages. These changes fix misleading/stale status handling;
they do not establish or eliminate a network/provider cause for that particular incident.
