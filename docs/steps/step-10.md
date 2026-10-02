# Step 10 — Privacy settings + explicit presentation mode
Design decision: presentation mode = hide on the user's OWN screen (overlay, history and privacy windows hidden; main window
minimized so the taskbar can always restore it; capture stopped; answering paused; visible red banner + tray icon state).
Explicit activation only (button / tray menu / global hotkey), OFF at every startup, capture never auto-resumes. Deliberately NO
OS capture-exclusion (SetWindowDisplayAffinity/WDA_*): that would make windows invisible to viewers of a shared screen, i.e. the
covert bypass the project forbids. A test scans the source for those APIs; scripts/live_presentation.py checks the running app.
Built: app/presentation.py (PresentationMode), app/hotkeys.py (GlobalHotkeys via RegisterHotKey + native event filter; parse_hotkey;
duplicate/conflict returns False), ui/tray.py (tray icon that changes colour/tooltip for presentation/incognito + menu),
privacy/state.py (PrivacyState: incognito, redact, wipe_all), privacy/redact.py (emails, phone numbers, Luhn-valid cards, SSNs,
API keys/tokens; conservative - plain numbers untouched), privacy/disclosure.py ("where your data goes" + consent text),
privacy/consent.py (ConsentGate: recording notice before first capture, remembered per PC), ui/privacy_dialog.py, main-window
banner (INCOGNITO purple / PRESENTATION red) + Privacy button. Jev masks outbound system prompt + messages (incl. history and the
classifier call) when redact is on, togglable live; local history keeps original text. HistoryStore.suspended (incognito) is
separate from the user's saving preference; CostTracker.persist + wipe(). AssistantController.set_paused blocks new questions.
Settings: INCOGNITO, REDACT_PII, REQUIRE_CONSENT, HOTKEY_PRESENTATION (ctrl+alt+p), HOTKEY_OVERLAY (ctrl+alt+o).
Tested: 261 tests pass (43 new: redaction cases/false positives/backtracking, Jev outbound masking, disclosure, consent, incognito,
wipe, presentation state machine, paused controller, hotkey parsing + native dispatch + real register/conflict, tray, dialog, banner).
Live: (1) black-box run of the real app - Ctrl+Alt+P hid the overlay and minimized the main window, Ctrl+Alt+O was ignored while
presenting, second Ctrl+Alt+P restored both; GetWindowDisplayAffinity == 0 (capturable) for both windows. (2) real GPT call:
redact OFF echoed "bob.smith@example.com / 415-555-0132 / sk-..."; redact ON echoed "[EMAIL] / [PHONE] / [SECRET]".
Known gaps: history.db is still unencrypted on disk (no SQLCipher; recommend BitLocker/Windows profile encryption or use
incognito); redaction is regex-based (names, addresses and free-form secrets are not detected); hotkeys need the combination to be
free system-wide; Anthropic path still untested live; the recording notice is an acknowledgement, not proof of others' consent.
