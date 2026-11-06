# Decision: light iOS-style glass theme (native Qt)

Date: 2026-11-07. Branch: `feature/ios-glass-ui`. Scope: UI styling only. No change to data, audio,
auth, fonts, answer typography, privacy policy or screen-capture behaviour.

## Choice
- Keep Qt Widgets with QSS + QPainter. No web migration and no native acrylic/DWM blur
  (Qt Widgets cannot blur what is behind a window; panels are translucent white over a
  mesh painted by the same window).
- Tokens live in `ui/tokens.py`: blue #4355dc, ink #232847, base #eceefc, pastel mesh
  b7c4ff/e3c6ff/ffd8c6/bde9ff, radii 26 panel / 22 header / 18 card / 12 control.
- `ui/surfaces.py`: `paint_panel`, `GlassBackdrop` (cached mesh pixmap, panels, optional rim/drift
  animation). `ui/theme.py`: QSS template, palette, `window_style()`, `status_color()`, `white()`.
- `ui/motion.py`: `MotionPolicy` read from Windows (client-area animation, transparency, high
  contrast). `override()`/`set_override()` exist for tests and the preview harness only; nothing
  is persisted and there are no new settings.

## Behaviour
- Animated: workspace windows only (dashboard, login, popups, onboarding). One 80 ms tick
  (12.5 fps, under the 15 fps cap). The 38 s mesh drift and 14 s rim rotation run only while the
  window is visible, not minimized and active. The overlay is static: no timer, static rim.
- Reduced motion or high contrast: no drift, static rim, toggle/page transitions jump.
- Reduced transparency: solid surfaces, solid QSS fills (`OPAQUE_STYLE`), mesh does not drift.
- High contrast: no glass stylesheet, system palette, system-coloured panels, status text uses
  `palette(window-text)`.
- Preferences are re-read on activation and on theme/palette change events, not by polling.
  Limitation: if Windows does not deliver such an event, a change made while the window stays
  active applies on the next activation.

## Contrast
`#767b93` fails AA for text (about 4.2:1) on the glass, so text uses `MUTED_TEXT` #585d77;
`#767b93` is kept for icons/borders. Tests check every text token against every surface over
every mesh colour (workspace) and over black/white/red/blue (overlay). The incognito banner keeps
its existing `#7a5cf5` (asserted by tests/test_privacy.py) at about 4.49:1 on bold white text.

## Rejected
- Dual dark/light theme (dead code per project rules): rollback is a branch revert.
- Per-frame full-window blur or `QGraphicsEffect` on large containers: costly on layered windows.

## Validation
`tests/test_ui_glass_theme.py`, reviewer `tests/test_glass_ui_review.py`, existing UI tests.
`scripts/preview_glass.py` renders fictional screens offscreen or on the native Windows backend
(`--policy`, `--hold`). Pre-existing, unrelated failures on the base commit: 10 tests in
test_composition, test_config, test_customer_byok and test_practice_matcher.
