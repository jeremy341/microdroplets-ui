"""Thread-safe authoritative driver-frequency state for one Multiboard."""

from __future__ import annotations

import threading

from backend.driver_capabilities import configured_capabilities


class DriverFrequencyState:
    """Store the last acknowledged F0/F1/F2 values for one connected board."""

    def __init__(self, initial: dict[int, int] | None = None) -> None:
        defaults = {
            index: max(cap.frequency_min_hz, min(cap.frequency_max_hz, 100))
            for index, cap in configured_capabilities().items()
        }
        if initial:
            defaults.update({int(key): int(value) for key, value in initial.items()})
        self._values = defaults
        self._lock = threading.RLock()

    def get(self, driver_index: int) -> int:
        with self._lock:
            try:
                return int(self._values[int(driver_index)])
            except KeyError as exc:
                raise ValueError("Driver index must be 0, 1, or 2") from exc

    def set(self, driver_index: int, value: int) -> None:
        driver_index = int(driver_index)
        if driver_index not in (0, 1, 2):
            raise ValueError("Driver index must be 0, 1, or 2")
        with self._lock:
            self._values[driver_index] = int(value)

    def replace(self, values: dict[int, int]) -> None:
        with self._lock:
            for driver_index, value in values.items():
                driver_index = int(driver_index)
                if driver_index not in (0, 1, 2):
                    raise ValueError("Driver index must be 0, 1, or 2")
                self._values[driver_index] = int(value)

    def snapshot(self) -> dict[int, int]:
        with self._lock:
            return dict(self._values)
