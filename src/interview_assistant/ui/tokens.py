"""Design tokens for the blue glass themes (colours, radii, surface opacity).

Two palettes, light and dark, share one set of names. ``use()`` switches the module-level
values; the theme is chosen once at startup (before any window is built), so every consumer
reads the active palette through ``tokens.NAME``. Changing the theme in Settings applies after a
restart, like the other settings.
"""

import sys
from dataclasses import dataclass

THEMES = ("system", "light", "dark")


@dataclass(frozen=True)
class Palette:
    blue: str  # the single action colour
    blue_dark: str  # text/borders that sit on blue-tinted surfaces
    on_blue: str  # text on a solid blue button
    ink: str
    ink_rgb: str  # "r,g,b" of ink for translucent overlays in QSS
    base: str  # window background under the mesh
    muted: str  # icons, borders, disabled hints
    muted_text: str  # secondary text (keeps AA contrast on every surface)
    disabled_text: str
    surface: str  # tint of translucent panels (blue glass, never pure white)
    surface_rgb: str
    sheen: int  # alpha of the top highlight on a panel
    edge: str  # 1 px panel outline
    edge_alpha: int
    mesh: tuple[str, str, str, str]
    ok: str
    warn: str
    error: str
    recording: str
    incognito: str
    solid_panel: str
    solid_sidebar: str
    solid_card: str
    solid_control: str
    code_bg: str
    code_fg: str
    shadow_rgb: str


LIGHT = Palette(
    blue="#3a4bd0",
    blue_dark="#2f3fb8",
    on_blue="#ffffff",
    ink="#1a2150",
    ink_rgb="26,33,80",
    base="#d3ddff",
    muted="#6a73a3",
    muted_text="#46507f",
    disabled_text="#5f6896",
    surface="#e6edff",
    surface_rgb="230,237,255",
    sheen=70,
    edge="#ffffff",
    edge_alpha=190,
    mesh=("#a9bcff", "#c9b9ff", "#a8d7ff", "#bfc8ff"),
    ok="#0f6b46",
    warn="#8a4f08",
    error="#b3261e",
    recording="#d32f2f",
    incognito="#7658ef",
    solid_panel="#e3eaff",
    solid_sidebar="#dce5ff",
    solid_card="#e0e8ff",
    solid_control="#eef2ff",
    code_bg="#1b2160",
    code_fg="#e7eaff",
    shadow_rgb="39,48,138",
)

DARK = Palette(
    blue="#8c9bff",
    blue_dark="#9aa8ff",
    on_blue="#0c1240",
    ink="#e8ecff",
    ink_rgb="232,236,255",
    base="#0d1235",
    muted="#8e99d6",
    muted_text="#b4bdf0",
    disabled_text="#8590cc",
    surface="#1b2459",
    surface_rgb="27,36,89",
    sheen=22,
    edge="#8fa0ff",
    edge_alpha=70,
    mesh=("#1a2680", "#43308f", "#14518a", "#202a78"),
    ok="#6fe0ae",
    warn="#ffc46b",
    error="#ff8f8f",
    recording="#ff6b6b",
    incognito="#9d87ff",
    solid_panel="#151c4a",
    solid_sidebar="#121843",
    solid_card="#182052",
    solid_control="#1d2760",
    code_bg="#0a0f35",
    code_fg="#e7eaff",
    shadow_rgb="0,0,0",
)

PALETTES = {"light": LIGHT, "dark": DARK}

# Radii (px)
RADIUS_PANEL = 26
RADIUS_HEADER = 22
RADIUS_CARD = 18
RADIUS_CONTROL = 12

# Surface opacity (0-255) by role. The overlay floats over arbitrary content, so it is nearly
# opaque; reduce-transparency makes every role opaque.
ALPHA_PANEL = 190
ALPHA_HEADER = 175
ALPHA_SIDEBAR = 150
ALPHA_CARD = 160
ALPHA_CONTROL = 205
ALPHA_OVERLAY = 244

MESH_PERIOD_SECONDS = 38.0
RIM_PERIOD_SECONDS = 14.0
WHITE = "#ffffff"  # only for knobs and text on solid colour

# Active values, set by use(). Names match Palette fields, upper-cased.
NAME = "light"
BLUE = LIGHT.blue
BLUE_DARK = LIGHT.blue_dark
ON_BLUE = LIGHT.on_blue
INK = LIGHT.ink
INK_RGB = LIGHT.ink_rgb
BASE = LIGHT.base
MUTED = LIGHT.muted
MUTED_TEXT = LIGHT.muted_text
DISABLED_TEXT = LIGHT.disabled_text
SURFACE = LIGHT.surface
SURFACE_RGB = LIGHT.surface_rgb
SHEEN = LIGHT.sheen
EDGE = LIGHT.edge
EDGE_ALPHA = LIGHT.edge_alpha
MESH = LIGHT.mesh
OK = LIGHT.ok
WARN = LIGHT.warn
ERROR = LIGHT.error
RECORDING = LIGHT.recording
INCOGNITO = LIGHT.incognito
PRESENTATION = LIGHT.recording
SOLID_PANEL = LIGHT.solid_panel
SOLID_SIDEBAR = LIGHT.solid_sidebar
SOLID_CARD = LIGHT.solid_card
SOLID_CONTROL = LIGHT.solid_control
CODE_BG = LIGHT.code_bg
CODE_FG = LIGHT.code_fg
SHADOW_RGB = LIGHT.shadow_rgb


def use(name: str) -> None:
    """Activate a palette ("light" or "dark"). Call before building windows."""
    palette = PALETTES[name]
    g = globals()
    g["NAME"] = name
    for field in (
        "blue",
        "blue_dark",
        "on_blue",
        "ink",
        "ink_rgb",
        "base",
        "muted",
        "muted_text",
        "disabled_text",
        "surface",
        "surface_rgb",
        "sheen",
        "edge",
        "edge_alpha",
        "mesh",
        "ok",
        "warn",
        "error",
        "recording",
        "incognito",
        "solid_panel",
        "solid_sidebar",
        "solid_card",
        "solid_control",
        "code_bg",
        "code_fg",
        "shadow_rgb",
    ):
        g[field.upper()] = getattr(palette, field)
    g["PRESENTATION"] = palette.recording


def system_prefers_dark() -> bool:
    """Windows' app colour mode (Settings > Personalization > Colors); light when unreadable."""
    if sys.platform != "win32":
        return False
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        ) as key:
            return winreg.QueryValueEx(key, "AppsUseLightTheme")[0] == 0
    except OSError:
        return False


def resolve(preference: str) -> str:
    """The palette name for a saved preference ("system", "light" or "dark")."""
    if preference == "dark":
        return "dark"
    if preference == "system" and system_prefers_dark():
        return "dark"
    return "light"
