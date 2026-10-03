# Step 20: Screenshot / vision support

## What was built
- `LLMRequest.image: bytes | None` (PNG bytes), attached to the answer request only. Classification
  never carries an image.
- Each provider's `build_payload()` adds its own native multimodal block to the **current**
  question (the last user message), leaving earlier conversation turns text-only:
  - OpenAI: `image_url` part with a `data:image/png;base64,...` URL.
  - Anthropic: base64 `image` block (before the text block).
  - Gemini: `inlineData` part (`mimeType: image/png`).
- `Jev.ask(question, decision, image=None)` passes the image into the answer request.
  `dataclasses.replace` in the router and in `Jev._outbound()` preserves it unchanged.
- `AssistantController.attach_screenshot(png)` holds one pending image. The next `ask()` takes and
  clears it, whichever path asked (typed, auto voice, or manual Answer). A redo ("Answer as...")
  reuses the image of the question it redoes. Pausing (presentation mode) or starting a new
  interview discards a pending image, and attaching while paused is ignored, so a stale screenshot
  cannot reach a later question. A `screenshot_pending` signal drives the UI state.
- `ui/screenshot.py`: `capture_screen_png()` grabs the primary screen as PNG bytes in memory. It is
  never written to disk and never sent unless a question is asked.
- Overlay: a `📷` header button and `Ctrl+Shift+S`. The button shows `📷 ✓` while a screenshot is
  pending. It is hidden in bubble mode like the other header buttons.
- Consent: screenshots have their own `ConsentGate` (key `consent/screenshot_v1`) with their own
  notice text. They do not reuse the recording notice, because that text is about other people's
  voices and says nothing about screen content going to a third party. Acknowledging recording does
  not acknowledge screenshots. The gate is wired into `MainWindow.attach_privacy()` via
  `overlay.before_screenshot`.
- `privacy/disclosure.py`: the data-flow line now reads "Questions, recent conversation and any
  screenshot you attach".

## Explicit-action guarantee
Screenshots are only taken from the button or the shortcut, after the consent gate passes. There is
no automatic or background capture.

## Vision-capable models (documentation)
The default fast and smart chains use model families that accept image input: GPT-4o and
GPT-4o-mini, Claude Sonnet and Haiku, and Gemini Flash models. Confirm specific model IDs against
the provider's current documentation before relying on them.

Known caveat: if you override `router_fast` / `router_smart` with a text-only model, a screenshot
question sent to it gets a 400 from that provider. The router then fails over to the next route in
the chain (it does not check `LLMError.retryable`), so the answer still arrives, but:
- each screenshot question wastes one failed call on the text-only model, and
- that 400 counts toward the provider's circuit breaker (`llm/router.py` `_Health`). Two in a row
  open its circuit for 30 s, which demotes it to last resort for other questions too.
Remove text-only models from the chain when you use screenshots.

## How it was tested
- Provider payloads (`test_llm.py`): each provider's image block shape, image on the last message
  only, text-only payloads unchanged, and the image reaching each provider's HTTP body.
- Jev (`test_jev.py`): the image is attached to the answer request, and a later question without a
  screenshot carries none.
- Controller (`test_app.py`): one-shot semantics, the pending-signal sequence, pause/new-interview
  discarding the image, attach-while-paused being ignored, manual Answer consuming it, and redo
  keeping it.
- Overlay (`test_app.py`): button attaches a capture, consent blocks capture, no-capture and
  no-controller are no-ops, pending state text, and bubble-mode hiding.
- Consent and composition (`test_privacy.py`, `test_composition.py`): the screenshot gate is
  independent of the recording gate, its notice is distinct, and it is wired to the overlay.
- End to end (`test_e2e.py`): overlay button -> controller -> Jev -> the real provider request
  object carries the screenshot for the next question only.
- Real desktop capture produced a valid PNG (not saved or sent).
- Full suite: 550 passed, 2 skipped (was 528). `ruff check` and `ruff format --check` clean for
  `src/` and `tests/`; `mypy src/` clean.

## Known gaps
- The screenshot is the full primary screen, not a selected window or region.
- No preview of the captured image before it is sent.
- `ruff check .` still reports one line-length error in `packaging/make_icon.py`. It predates this
  step and was left alone.
