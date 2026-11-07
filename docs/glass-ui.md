# Desktop glass refresh — v0.1.1

**Superseded:** main now uses the blue glass light and dark themes (decision
[006](decisions/006-blue-glass-light-dark-theme.md), merged as `172bd33`), built on the light glass
theme merged as `67db178` on 7 November 2026 (see the sections below). The navy/cyan
palette described in the rest of this first part is historical. Window sizing, title bar
and native-sizing rules still apply. See [current status](PROJECT_STATUS.md).

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

## Light iOS glass theme (feature/ios-glass-ui, 2026-11-07)

Supersedes the navy/cyan palette above. A pastel mesh (painted per window) sits behind
translucent white panels with bright edges; the blue rim is animated only on active workspace
windows and static on the overlay. Reduced motion, reduced transparency and high contrast are read
from Windows (`ui/motion.py`). Decision, tokens, contrast results and limits:
`docs/decisions/ios-glass-ui.md`. Preview: `python scripts/preview_glass.py --platform windows
--policy default|reduced-motion|reduced-transparency|high-contrast [--hold SECONDS]`.

Reference look: the Orbit web app (`public/styles.css`). Its base `#eceefc`, ink `#232847`,
accent `#4355dc`, pastel mesh and blue rim are carried over through `ui/tokens.py`. Qt Widgets
cannot blur the desktop, so panels are translucent white over a painted mesh rather than CSS
`backdrop-filter`. Orbit's Aptos font is not bundled; the app keeps Segoe UI.

### Integration evidence (7 November 2026)

- Merge `67db178` into main had no conflicts; pushed to `origin/main`.
- Before merging: ruff clean, mypy clean on the UI package, and the glass, review,
  screen-share, privacy, security-hygiene and onboarding tests gave 162 passed, 2 skipped.
- Full suite on merged main: **6 failed, 1689 passed, 5 skipped** (35 minutes). None is caused
  by the glass merge:
  - `test_app.py::test_overlay_window_flags_and_visibility_contract` fails identically on the
    pre-merge baseline `ef3b598`. It forbids `SetWindowDisplayAffinity` in all source, which
    the authorized screen-share adapter (`privacy/capture_visibility.py`) now contains.
  - `test_composition_shares_verified_catalog_with_wizard`, the two `test_config.py` dialog
    tests and the `test_customer_byok.py` key test are the known stale model-list failures
    recorded in `PROJECT_STATUS.md` (not re-run on the baseline here).
  - `test_continuation.py::test_output_limit_is_incomplete...[length]` timed out waiting 5 s
    during the long run, and passes alone on both trees. Treated as a load-dependent flake.
- Native Windows previews with fictional data covered the dashboard, session wizard, settings,
  login, setup wizard, model popup and overlay in the default policy.
- Not yet checked: reduced-transparency and high-contrast previews on the merged build, and
  the screen-share indicator when On on the light overlay. No installer has been rebuilt.

## Blue glass light and dark themes (7 November 2026)

Decision: [006](decisions/006-blue-glass-light-dark-theme.md). Merged as `172bd33`.

- Two palettes in `ui/tokens.py`. `tokens.use("light" | "dark")` switches them once at startup;
  stylesheets are built from the active tokens on demand, so no module keeps a stale palette.
- Panels are blue-tinted glass, not pure white, in both themes. The overlay reading surface is
  nearly opaque (alpha 244).
- Setting: Settings > Appearance > Theme (Light blue default, Dark blue, Match Windows). It
  applies after a restart. Customer builds start on the Windows mode at login, then use the
  account's saved choice before the workspace is built.
- Overlay: a 4 px band along the top of the answer panel shows state (idle muted, listening
  recording red, thinking amber, answering green). Answer is the single filled button;
  Screenshot and Chat are quiet; End is outlined red. Recording and presentation indicators
  are unchanged.
- Preview both themes: `python scripts/preview_glass.py --platform windows --theme light|dark`.

Contrast tests now run each text token against each surface over each mesh colour in both
palettes. They caught the blue text token on the light sidebar (4.42:1) and on the dark panels
(2.9:1); values were tuned until all passed.

Evidence: theme tests 106 passed; the wider app/UI files passed apart from the failures below;
a quiet rerun of the app, start-screen, provider-picker, session-dialog, composition and e2e
files gave 265 passed, 2 failed. The two failures also fail without this change:
`test_overlay_window_flags_and_visibility_contract` (forbids `SetWindowDisplayAffinity`, which
the authorized screen-share adapter uses) and `test_composition_shares_verified_catalog_with_wizard`
(expects the removed `gpt-5.4-mini`). `test_e2e.py::test_spoken_question_flows_to_answer_history_and_cost`
failed once while other jobs were loading the machine and passed on every quiet run.

Not done: the mockup's "Up next" dashboard card (the bottom Start button is the selected-session
action tests use); a larger default answer font (answer typography stays under the user's
controls); reduced-transparency and high-contrast previews of the new palettes; the screen-share
indicator on the new overlay when On; an installer build. The installed app keeps the old theme.
