# Step 2 — Audio capture
Built: audio/{types,devices,convert,capture,vad}.py; ui/audio_panel.py (device picker, explicit Start/Stop,
red "RECORDING" indicator, level meter, speech indicator, segment counter); scripts/audio_smoke.py.
- Devices: WASAPI mic + loopback (system audio) via PyAudioWPatch.
- Pipeline: native PCM -> mono 16 kHz int16 -> level + energy VAD (hysteresis, pre-roll, max length) -> segments.
- Capture never starts without the user pressing Start; UI always shows recording state.
Tested: 17 tests pass (conversion, VAD, capture with fake source, UI flow/errors); live smoke: played tone captured via
loopback (1 segment, peak 0.29); mic opened OK. mypy target bumped to 3.12 (numpy stubs).
Known gaps: VAD is energy-based (threshold fixed 0.01); no mic+loopback mixing yet; no persistence of chosen device.
