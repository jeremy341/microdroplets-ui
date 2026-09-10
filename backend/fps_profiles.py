"""Legacy/pure helpers for camera FPS profile calculations.

The current AM4113T(R9) UI uses fixed mode presets and does not probe FPS to
decide which options appear. These helpers remain for profile-file/test
compatibility. See ``docs/DEVELOPER_GUIDE.md``.
"""

from __future__ import annotations

from statistics import median
from typing import Iterable


FPS_STEP = 10
MIN_UI_FPS = 10
MAX_UI_FPS = 120
FPS_TARGET_TOLERANCE = 0.15


def stable_fps(samples: Iterable[float]) -> float:
    """Return a robust FPS estimate without allowing one peak to dominate."""

    values = sorted(float(value) for value in samples if float(value) > 0)
    if not values:
        return 0.0
    if len(values) >= 5:
        # Remove one high and one low outlier before calculating the median.
        values = values[1:-1]
    return round(float(median(values)), 2)


def rounded_ui_ceiling(actual_fps: float, *, step: int = FPS_STEP) -> int:
    """Round to the nearest UI step, with 5 and above rounding upward."""

    actual = float(actual_fps or 0.0)
    if actual <= 0:
        return MIN_UI_FPS
    rounded = int((actual + (step / 2.0)) // step) * step
    return max(MIN_UI_FPS, min(MAX_UI_FPS, rounded))


def ui_fps_options(actual_fps: float, *, step: int = FPS_STEP) -> list[int]:
    ceiling = rounded_ui_ceiling(actual_fps, step=step)
    return list(range(step, ceiling + 1, step))


def target_is_usable(requested_fps: int, measured_fps: float) -> bool:
    """Decide whether a requested preset is honestly selectable.

    The 35% loss threshold remains a warning/quality boundary.  A dropdown
    preset uses a tighter 15% target tolerance so that choosing 40 FPS does
    not silently produce a 30-FPS stream.  A slow mode is still represented by
    the mandatory 10-FPS fallback with an actual-FPS annotation.
    """

    requested = max(1, int(requested_fps))
    measured = float(measured_fps or 0.0)
    lower = requested * (1.0 - FPS_TARGET_TOLERANCE)
    upper = requested * (1.0 + FPS_TARGET_TOLERANCE)
    return lower <= measured <= upper


def format_fps_option(requested_fps: int, actual_fps: float | None = None) -> str:
    requested = int(requested_fps)
    if actual_fps is None or actual_fps <= 0:
        return f"{requested} FPS"
    if abs(float(actual_fps) - requested) <= 1.0:
        return f"{requested} FPS"
    return f"{requested} FPS · actual ≈ {float(actual_fps):.0f}"
