"""Robust signal conditioning for the SLF3S liquid-flow stream."""

from __future__ import annotations

import math
from collections import deque


class FlowSignalFilter:
    """Remove isolated motion spikes while retaining sustained flow."""

    def __init__(self, *, ema_time_constant_seconds=0.35,
                 zero_deadband_ul_min=75.0, confirmation_samples=2):
        if ema_time_constant_seconds <= 0 or zero_deadband_ul_min < 0:
            raise ValueError("Invalid flow-filter parameters")
        self.ema_time_constant_seconds = float(ema_time_constant_seconds)
        self.zero_deadband_ul_min = float(zero_deadband_ul_min)
        self.confirmation_samples = max(1, int(confirmation_samples))
        self._window = deque(maxlen=3)
        self._ema = None
        self._valid_run = 0
        self._active = False

    @property
    def active(self):
        return self._active

    def reset(self):
        self._window.clear()
        self._ema = None
        self._valid_run = 0
        self._active = False

    def update(self, raw_value_ul_min, delta_seconds):
        value = float(raw_value_ul_min)
        if not math.isfinite(value):
            value = 0.0
        self._window.append(value)
        ordered = sorted(self._window)
        median = ordered[len(ordered) // 2]

        if not self._active:
            if abs(median) <= self.zero_deadband_ul_min:
                self._valid_run = 0
                self._ema = 0.0
                return 0.0
            self._valid_run += 1
            if self._valid_run < self.confirmation_samples:
                self._ema = 0.0
                return 0.0
            self._active = True
            self._ema = median
            return median

        if abs(median) <= self.zero_deadband_ul_min:
            self.reset()
            return 0.0
        if self._ema is None or delta_seconds is None or delta_seconds <= 0:
            self._ema = median
        else:
            alpha = 1.0 - math.exp(-delta_seconds / self.ema_time_constant_seconds)
            self._ema += alpha * (median - self._ema)
        return self._ema
