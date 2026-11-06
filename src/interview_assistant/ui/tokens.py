"""Design tokens for the light glass theme (colours, radii, surface opacity)."""

# Brand
BLUE = "#4355dc"
BLUE_DARK = "#3140b8"
INK = "#232847"
BASE = "#eceefc"
# MUTED is for icons, borders and disabled hints. Text uses MUTED_TEXT, which is darker so that
# it keeps WCAG AA contrast on every translucent surface (tests/test_ui_glass_theme.py).
MUTED = "#767b93"
MUTED_TEXT = "#585d77"
DISABLED_TEXT = "#7b8098"
WHITE = "#ffffff"

# Pastel mesh, in paint order
MESH = ("#b7c4ff", "#e3c6ff", "#ffd8c6", "#bde9ff")

# Status colours chosen for text on the light surfaces
OK = "#17714a"
WARN = "#92540a"
ERROR = "#b3261e"
RECORDING = "#d32f2f"
INCOGNITO = "#7a5cf5"  # existing indicator colour, asserted by tests/test_privacy.py
PRESENTATION = "#d32f2f"

# Radii (px)
RADIUS_PANEL = 26
RADIUS_HEADER = 22
RADIUS_CARD = 18
RADIUS_CONTROL = 12

# White surface opacity (0-255) by role; reduce-transparency makes all of them opaque.
ALPHA_PANEL = 190
ALPHA_HEADER = 175
ALPHA_SIDEBAR = 150
ALPHA_CARD = 160
ALPHA_CONTROL = 205
ALPHA_OVERLAY = 238

# Opaque stand-ins used when the OS asks for reduced transparency
SOLID_PANEL = "#f8f9ff"
SOLID_SIDEBAR = "#f1f3fe"
SOLID_CARD = "#f4f5ff"
SOLID_CONTROL = "#ffffff"

MESH_PERIOD_SECONDS = 38.0
RIM_PERIOD_SECONDS = 14.0
