# Step 7 — Compact overlay + end-to-end pipeline
Built: app/async_runner.py (asyncio loop on a daemon thread), app/aggregator.py (merges split utterances; releases a question
when nothing is still transcribing + 0.5s quiet, or after 8s max hold; stale in-flight starts expire), app/controller.py
(AssistantController: STT finals -> aggregator -> Jev.decide -> commit -> stream; generation-tagged Qt signals; filler ignored
without touching the answer on screen; new question cancels the running answer; stale slow decisions dropped; Stop; friendly
errors), ui/overlay.py (frameless always-on-top, draggable, resizable, opacity slider, collapse, throttled markdown streaming,
Ask box, Stop/Esc, Copy, status dot, category/route/latency chip, geometry+opacity persisted via QSettings), main window wiring
(Show overlay, "New interview" clears context), __main__ composition. Jev.ask(question, decision=) avoids double classification.
The overlay is a normal capturable window; nothing calls SetWindowDisplayAffinity (a test enforces this). Presentation mode is Step 10.
Tested: 156 tests pass (aggregator, runner, controller ordering/cancel/stale/stop/errors/merge, overlay behaviour, integration with real Jev + scripted providers).
Live E2E (scripts/e2e_live.py: TTS -> speakers -> loopback -> local Whisper -> Jev -> GPT-4o -> overlay): two spoken fragments merged
into one question, classified behavioral, answered in the overlay; screenshots reviewed. First answer token 2.5s after speech end
with local Whisper base.en vs 3.9s with OpenAI STT (network) -> local stays default.
Known gaps: STT dominates latency (~2s CPU) - Step 13 should look at GPU/faster models/streaming STT; base.en mishears technical
terms ("rate-limeter") - consider small.en or a vocabulary prompt; no global hotkey (in-app Esc only; needs a hook lib);
mic + system audio are still one source at a time (no speaker labelling); overlay position/size restored but not multi-monitor
validated; Anthropic untested live.
