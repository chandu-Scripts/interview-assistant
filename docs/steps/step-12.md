# Step 12 — Packaging, installer and smoke tests
Built: PyInstaller onedir spec (packaging/interview_assistant.spec: bundles PySide6 widgets, faster-whisper + Silero VAD model,
ctranslate2, onnxruntime, certifi, keyring backends, pyaudiowpatch; trims ~75 MB of unused Qt/Pillow), launcher, generated icon
(packaging/make_icon.py -> app.ico), Windows version resource, scripts/build.py (icon -> version -> PyInstaller with log capture ->
static DLL check -> packaged self-test), scripts/check_dist_deps.py (pefile-based: every hard-linked DLL must be bundled or provided
by Windows; runs even where unsigned exes are blocked), Inno Setup installer (installer/interview_assistant.iss: per-user, no admin,
Start-menu/optional desktop shortcut, uninstall asks whether to also delete data via `--purge-data`; silent uninstall keeps data
unless /PURGE) + scripts/build_installer.py, scripts/sign.py (signtool wrapper, env-configured; NOT exercised: no certificate),
docs/packaging.md. App side: cli.py (`--version`, `--self-test [--deep] [--out]`, `--purge-data`), selftest.py (Qt plugins, app
modules, settings, data dir, SQLite FTS5, keyring backend, TLS CA bundle, Whisper + VAD asset, onnxruntime, audio; --deep adds real
HTTPS reachability and a real Whisper run), purge.py, frozen-aware relaunch.
Measured: build 312 MB / 235 files (was 373 MB / 350 before trimming); installer 84 MB; windows appear ~2.1-2.6 s after launch.
Tested: 322 unit tests pass (19 new: CLI, self-test, purge with injected stores, frozen relaunch, spec/installer content, version
resource, dependency checker on a synthetic bundle). Packaged app: self-test OK (frozen=True), --deep OK (TLS to OpenAI/Anthropic/
Gemini reachable, Whisper base.en loaded in 0.8 s and transcribed silence to ''), black-box UI test PASS (hotkey presentation mode,
display affinity 0). Installer: compiled with Inno Setup 6.7.3 (installed with the user's permission), silent install -> 237 files,
installed exe self-test OK + black-box UI PASS, silent uninstall removed the folder and registry entry, real data untouched.
Findings: (1) Smart App Control is ON on this PC and intermittently blocks freshly built UNSIGNED exes (WinError 4551,
CodeIntegrity events 3077/3033) - other PCs will need a signed build; (2) my first size-trimming broke two Qt plugins, caught by
the static dependency check (dropped them too); (3) a version-resource / --clean build hiccup was transient (locked files).
Known gaps: no code signing (needs a certificate), so the release build/installer will be blocked on Smart-App-Control PCs;
interactive uninstall prompt and the /PURGE data-removal path were compiled but not exercised end to end (they would touch the real
credential store); Whisper models download on first use (not bundled); no auto-update; CPU-only; Anthropic still untested live;
dist/ (312 MB) and dist/installer (84 MB) are build outputs, git-ignored.
