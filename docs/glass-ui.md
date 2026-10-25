# Desktop glass refresh — v0.1.1

Requested on 2026-10-25 after reviewing the owner's eight Parakeet reference photos.

The dashboard opens centered at 960 × 680 logical pixels, reduced for smaller work areas.
It starts in normal window mode, with a draggable title bar, minimize/maximize/close buttons,
and a bottom-right resize grip. Compact session cards replace the wide visible table columns;
search, filters, selection, start/resume, and session review continue to use the existing data.

Shared navy/cyan styling covers the dashboard, session wizard, settings and auxiliary windows.
The floating toolbar and answer panels use rounded, translucent gradient surfaces. This is a
Qt-painted glass appearance with subtle transparency, not a native iOS material or desktop blur.
Text remains readable independently of wallpaper. Audio and presentation behavior is unchanged.

Wrapped labels in hidden pages previously propagated a height-for-width constraint to the native
window. The dashboard delegates wrapping/scrolling to its pages so Windows honors compact sizes.

Validation:

- Full suite: 658 passed, 2 skipped. Windows credential roundtrip requires an unsandboxed login.
- Focused dashboard/session suite after the native geometry fix: 30 passed.
- Ruff passed; mypy passed for 87 source files.
- Real Windows previews used fictional sessions and no audio/API calls. Verified 960 × 680
  dashboard and 900 × 560 preferences, with an explicit size assertion at the smaller size.
- Screenshots: `tmp/dashboard-preview/` and `tmp/overlay-preview/` (not committed user data).
- Full source-app native startup and single-instance check passed in an isolated data directory.
- Packaged DLL validation: 186 binaries passed. Executable startup was blocked by Windows
  Application Control, WinError 4551. No code-signing certificate was found in the current-user
  or local-machine personal stores. Packaged startup is not verified; no protections were changed.
- The website remains on its previous v0.1.0 deployment; local v0.1.1 release content is a draft.
- Final packaged archive was inspected to confirm the palette module and startup initializer.
  `dist/installer/InterviewAssistant-Setup-0.1.1.exe` compiled successfully; it was not launched
  or published because the executable requires signing and packaged runtime validation.
- A separate `Chandoo - Glass Preview.lnk` desktop shortcut launches the updated local source
  through `.venv/Scripts/pythonw.exe`; close the installed app before using it.

The installer remains an unsigned personal development build. Upgrading requires installing
v0.1.1; an already running or installed v0.1.0 copy will not change automatically.
