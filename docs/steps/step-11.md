# Step 11 — Settings, onboarding and key management
Built:
- config/keys.py: KeyStore protocol, KeyringStore (Windows Credential Manager), MemoryKeyStore (tests), key_status/mask (env vs
  stored vs none; env always wins and is shown read-only). config/loader.py load_settings(): fills keys from the credential store
  where the environment provides none.
- config/user_settings.py: settings.json (atomic writes, only whitelisted non-secret fields, validated before writing, corrupt file
  quarantined as .json.bad) plugged into pydantic-settings as the lowest-priority source (init > env > .env > json > defaults).
- config/provider_check.py: free "list models" call per provider validates a key (401/403 -> rejected, 429 -> accepted, network
  errors reported, key never in messages/URLs) and returns the models the account actually offers (filtered to chat models).
- ui/settings_dialog.py (tabs: AI providers + models + chains, Speech, Profile, Budget & history, Privacy & hotkeys): per-provider
  Test/Save/Remove, model dropdowns filled from a successful test, validation (hotkeys, route chains, cloud STT needs an OpenAI key),
  saves only non-default values, reports whether a restart is needed; main window "Settings" button; the app offers to restart itself.
- ui/onboarding.py first-run wizard (welcome + recording notice, keys with Test, speech: local model size / cloud + "download model
  now", audio source, profile, budget, incognito). SKIP_ONBOARDING for automation. AudioPanel pre-selects the configured audio kind.
- app/restart.py: relaunch happens only after the event loop exits and hotkeys/history are released.
Settings added: DEFAULT_AUDIO_KIND, SKIP_ONBOARDING (and every EDITABLE setting is now editable in the app).
Tested: 303 tests pass (42 new: settings file, precedence, keys/status/masking, real Credential Manager round-trip under a throwaway
service name, provider checks for all three vendors, KeyRow, dialog validation/persistence/restart flags, wizard rules, audio-kind,
restart). Live: OpenAI and Gemini keys accepted, bad keys rejected without echo; all four default model names confirmed available on
this account; screenshots of every settings tab and wizard pages reviewed; black-box presentation-mode test still PASSES on the
rebuilt startup path.
Known gaps: Anthropic key check untested against the real API (no key); changing settings needs a restart (only privacy toggles are
live); the Gemini list can still include preview models that are poor chat choices; key hint shows first 3 + last 4 chars;
"import my .env key into Credential Manager" is not automated (the dialog shows env keys as read-only); no per-model
price editing UI (PRICING_FILE only); no settings export/import.
