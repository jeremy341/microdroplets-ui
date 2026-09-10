"""Calibration helpers shared by the UI and analysis backend."""

from __future__ import annotations

import math

from .models import Calibration


def scale_from_reference(pixel_length: float, known_length_um: float, resolution: tuple[int, int]) -> Calibration:
    pixel_length = float(pixel_length)
    known_length_um = float(known_length_um)
    if not math.isfinite(pixel_length) or pixel_length <= 0:
        raise ValueError("Reference pixel length must be greater than zero.")
    if not math.isfinite(known_length_um) or known_length_um <= 0:
        raise ValueError("Known reference length must be greater than zero.")
    return Calibration(
        um_per_px=known_length_um / pixel_length,
        resolution=(int(resolution[0]), int(resolution[1])),
        source="reference",
    )


def manual_scale(um_per_px: float, resolution: tuple[int, int]) -> Calibration:
    value = float(um_per_px)
    if not math.isfinite(value) or value <= 0:
        raise ValueError("Scale must be greater than zero.")
    return Calibration(value, (int(resolution[0]), int(resolution[1])), "manual")
