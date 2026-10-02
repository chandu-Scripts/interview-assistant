# Step 3 — Streaming transcription
Built: stt/{base,local_whisper,openai_stt,streaming,factory}.py; VAD now emits growing partials
(VadConfig.partial_interval_ms=1500); AudioCapture(on_partial=...); ui/transcript_panel.py (finals normal,
ongoing partial grey italics); MainWindow wires audio -> StreamingTranscriber -> transcript panel.
- Providers behind STTProvider: local faster-whisper (default, offline, base.en int8 CPU) and OpenAI
  (gpt-4o-mini-transcribe, only if STT_PROVIDER=openai + key; errors never include key/body).
- StreamingTranscriber: single worker; latest partial replaces stale queued partial; finals always run first
  and supersede partials; errors reported without killing the worker; stop() drains finals.
- Settings: STT_PROVIDER, WHISPER_MODEL, STT_LANGUAGE.
Tested: 29 tests pass (ordering, staleness, errors, drain, OpenAI via mock transport, factory, UI pipeline).
Real: TTS question transcribed exactly from file; silence -> ''; live loopback E2E produced partial then final
transcripts (final ~2.8s after speech end on CPU base.en).
Known gaps: OpenAI path untested against the live API (no key); no GPU use (MX450, CPU int8 chosen);
no speaker separation (interviewer vs. user) — mic and loopback are still single-source; partials re-transcribe the
whole utterance (fine for <=30s segments).
