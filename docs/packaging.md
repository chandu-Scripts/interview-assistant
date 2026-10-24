# Packaging and installing

## Build
```
python scripts/build.py              # PyInstaller onedir build -> dist/InterviewAssistant/
python scripts/build_installer.py    # Inno Setup installer   -> dist/installer/InterviewAssistant-Setup-<ver>.exe
```
`build.py` does: icon -> Windows version resource -> PyInstaller -> **static DLL dependency check**
(`scripts/check_dist_deps.py`, fails the build if any hard-linked DLL is missing) -> **packaged
self-test** (`InterviewAssistant.exe --self-test`).

After a successful build, run `python scripts/smoke_packaged.py` to check the native dashboard
and second-launch handling with a temporary data directory under `tmp/`. It never starts audio
capture and terminates only the processes it launches. The JSON report stays in that directory.

Why PyInstaller onedir: no C compiler needed on Python 3.14, reliable with PySide6/ctranslate2/onnxruntime,
fast start (windows appear in ~2-3 s), and easy to inspect. Nuitka was not needed.

## What is bundled
PySide6 (widgets only), faster-whisper + ctranslate2 + onnxruntime (with the Silero VAD model), PyAV (a faster-whisper
import), numpy, httpx + certifi (HTTPS), pydantic, keyring (Windows Credential Manager), pyaudiowpatch (WASAPI loopback).
Whisper **models are not bundled**: the first-run wizard downloads the chosen one (base.en ~150 MB) to the Hugging Face cache.
Trimmed on purpose: software OpenGL, Qt Quick/QML/PDF runtimes, Qt translations, Pillow (~75 MB saved; the static check
guards the trimming).

## Switches (work from source and from the exe)
```
InterviewAssistant.exe --version
InterviewAssistant.exe --self-test [--deep] [--out result.json]   # exit code 0 = ok; --deep = HTTPS + Whisper run
InterviewAssistant.exe --purge-data                               # deletes data folder, stored API keys, saved UI state
```
The exe is a windowed program, so use `--out FILE` to capture output in scripts.
`SKIP_ONBOARDING=true` and `DATA_DIR=<folder>` help automation. Black-box UI test of a build:
`APP_EXE=dist\InterviewAssistant\InterviewAssistant.exe SKIP_ONBOARDING=1 DATA_DIR=%TEMP%\ia python scripts/live_presentation.py`.

## Installer behaviour
Per-user install (no admin) to `%LOCALAPPDATA%\Programs\Interview Assistant`, Start-menu entry, optional desktop icon.
Uninstall asks whether to also delete saved settings, history and stored API keys (runs `--purge-data`); a silent
uninstall keeps data unless `/PURGE` is passed. Data lives in `%APPDATA%\InterviewAssistant`.

## IMPORTANT: Smart App Control / code signing
Windows 11 **Smart App Control** (and WDAC policies) block unsigned executables they have no reputation for
(`WinError 4551: An Application Control policy has blocked this file`). On the development PC this blocked freshly
built, unsigned exes intermittently. For other people's PCs the app **and installer must be signed** with a certificate Windows
trusts (OV/EV code-signing certificate or Azure Trusted Signing); a self-signed certificate does not help.
```
set SIGN_PFX=C:\certs\release.pfx & set SIGN_PASSWORD=...     (or SIGN_THUMBPRINT=...)
python scripts/build.py && python scripts/sign.py && python scripts/build_installer.py && python scripts/sign.py dist\installer
```
Do not disable Smart App Control to work around this: Windows cannot turn it back on without reinstalling.
Alternatives to signing: run from source (`python -m interview_assistant`, works everywhere Python is allowed) or
publish through the Microsoft Store (MSIX).

## Known limits
- Build is ~312 MB unpacked, 84 MB as an installer (lzma2): native AI runtimes dominate.
- CPU-only Whisper (CTranslate2 GPU needs CUDA libraries; not bundled).
- Windows 10/11 x64 only.
