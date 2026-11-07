# Decision 006: blue glass light and dark themes, reading-first overlay

Date: 7 November 2026. Branch: `feature/reading-first-theme`. Scope: UI styling and one setting.
No change to audio, auth, answers, privacy behaviour or answer typography controls.

## Requests
- "More beautiful design" after the light glass theme (decision `ios-glass-ui.md`) was merged.
- Add a dark theme.
- Make the glass blue, not pure white.

## Choice
- Two palettes (`LIGHT`, `DARK`) in `ui/tokens.py` with the same field names. `tokens.use(name)`
  switches the module-level values once at startup, before any window exists. Every consumer
  reads `tokens.NAME`, and stylesheets are built on demand (`theme.STYLE`, `overlay.build_style()`),
  so nothing keeps a stale copy of the other palette.
- Surfaces are tinted blue in both themes (`SURFACE`), never pure white. The overlay reading
  surface is nearly opaque (alpha 244) so text never depends on what is behind it.
- New setting `theme` (`light` default, `dark`, `system`) in Settings > Appearance. It applies
  after a restart, like other settings. `system` follows Windows' app colour mode. Customer
  builds start on the Windows mode on the login screen, then use the account's saved choice
  before the workspace is built.
- Overlay: a 4 px band along the top of the answer panel shows state (idle muted, listening
  recording red, thinking amber, answering green). Listening keeps the recording colour on
  purpose, so the explicit recording indicator is unchanged. Answer is the single filled
  button; Screenshot and Chat are quiet; End is outlined red.

## Reverses
`ios-glass-ui.md` rejected a dual dark/light theme as dead code. The user has since asked for
both, so that rejection no longer applies. Rollback is a branch revert.

## Rejected
- Real backdrop blur: Qt Widgets cannot blur the desktop; panels stay translucent tint over a
  painted mesh.
- Live theme switching without restart: windows would need rebuilding. Not worth the risk.
- A new overlay layout from scratch: the existing widgets, object names and tests are kept.

## Contrast
`tests/test_ui_glass_theme.py` now runs every text token against every surface over every mesh
colour in both palettes (AA 4.5:1), plus filled controls. Tuning found two failures: the blue
text token on the light sidebar (4.42) and on the dark panels (2.9). Values were adjusted
(`#3a4bd0` light, `#8c9bff` dark, with a darker dark mesh) until all passed.

## Not done in this change
- The "Up next" card on the dashboard from the mockup. The bottom Start button starts the
  selected session and tests use it, so it was left unchanged.
- Larger default answer text. Answer font controls belong to the user; the default is unchanged.

## Validation
Contrast, stylesheet, state-edge and setting tests; native Windows previews for both themes
(`python scripts/preview_glass.py --platform windows --theme light|dark`). Receiver and
high-contrast behaviour were checked as described in `docs/glass-ui.md`.
