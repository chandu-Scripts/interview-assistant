"""Accessibility policy for glass motion and transparency.

The Windows preferences are read directly (no new app setting). A harness can force values with
``override``; nothing is persisted.
"""

import ctypes
import logging
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace

logger = logging.getLogger(__name__)

_CACHE_SECONDS = 1.0
_SPI_GETHIGHCONTRAST = 0x0042
_SPI_GETCLIENTAREAANIMATION = 0x1042
_HCF_HIGHCONTRASTON = 0x1


@dataclass(frozen=True)
class MotionPolicy:
    reduce_motion: bool = False
    reduce_transparency: bool = False
    high_contrast: bool = False

    @property
    def animations_allowed(self) -> bool:
        return not (self.reduce_motion or self.high_contrast)

    @property
    def mesh_drift(self) -> bool:
        # With reduced transparency the backdrop stays still behind its opaque panels.
        return self.animations_allowed and not self.reduce_transparency

    @property
    def use_mesh(self) -> bool:
        return not self.high_contrast


class _HighContrast(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_uint),
        ("dwFlags", ctypes.c_uint),
        ("lpszDefaultScheme", ctypes.c_wchar_p),
    ]


def _read_os_policy() -> MotionPolicy:
    if sys.platform != "win32":
        return MotionPolicy()
    reduce_motion = reduce_transparency = high_contrast = False
    try:
        user32 = ctypes.windll.user32  # type: ignore[attr-defined,unused-ignore]
        animation = ctypes.c_int(1)
        if user32.SystemParametersInfoW(_SPI_GETCLIENTAREAANIMATION, 0, ctypes.byref(animation), 0):
            reduce_motion = animation.value == 0
        contrast = _HighContrast(ctypes.sizeof(_HighContrast), 0, None)
        if user32.SystemParametersInfoW(
            _SPI_GETHIGHCONTRAST, ctypes.sizeof(contrast), ctypes.byref(contrast), 0
        ):
            high_contrast = bool(contrast.dwFlags & _HCF_HIGHCONTRASTON)
    except (OSError, AttributeError):
        logger.debug("Could not read Windows animation/contrast preferences", exc_info=True)
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        ) as key:
            reduce_transparency = winreg.QueryValueEx(key, "EnableTransparency")[0] == 0
    except OSError:
        pass  # value absent: Windows default is transparency on
    return MotionPolicy(reduce_motion, reduce_transparency, high_contrast)


_override: MotionPolicy | None = None
_cached: tuple[float, MotionPolicy] | None = None


def current() -> MotionPolicy:
    """The effective policy. OS values are cached for a second so ticks stay cheap."""
    global _cached
    if _override is not None:
        return _override
    now = time.monotonic()
    if _cached is None or now - _cached[0] > _CACHE_SECONDS:
        _cached = (now, _read_os_policy())
    return _cached[1]


def refresh() -> None:
    """Forget the cached OS preferences (called on theme/palette/activation events)."""
    global _cached
    _cached = None


def set_override(policy: MotionPolicy | None) -> None:
    """Force a policy for tests/preview scripts; None restores the OS preferences."""
    global _override
    _override = policy


@contextmanager
def override(
    *,
    reduce_motion: bool | None = None,
    reduce_transparency: bool | None = None,
    high_contrast: bool | None = None,
) -> Iterator[MotionPolicy]:
    """Temporarily force fields (others keep their current value)."""
    global _override
    previous = _override
    base = current()
    fields = {
        "reduce_motion": reduce_motion,
        "reduce_transparency": reduce_transparency,
        "high_contrast": high_contrast,
    }
    _override = replace(base, **{k: v for k, v in fields.items() if v is not None})
    try:
        yield _override
    finally:
        _override = previous
