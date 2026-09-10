"""Translate between live board dictionaries and safe session configuration.

No function in this module sends a command or opens a device.  It is therefore
safe to use during Save/Load without accidentally starting laboratory hardware.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Iterable

from backend.protocol import (
    DRIVER_AMPLITUDE_LIMITS,
    DRIVER_FREQUENCY_LIMITS,
    WAVEFORM_ALIASES,
    WAVEFORM_CODES,
)


def build_board_profile(board: dict[str, Any]) -> dict[str, Any]:
    channels = []
    for driver in board.get("drivers", []):
        driver_index = int(driver.get("driver_index", 0))
        for channel in driver.get("channels", []):
            channels.append({
                "channel": int(channel["channel"]),
                "driver_index": driver_index,
                "frequency_hz": int(driver.get("frequency", 100)),
                "amplitude_vpp": int(channel.get("amplitude", 100)),
                "waveform": str(driver.get("waveform", "Sinus")),
                # Runtime hardware state is deliberately never persisted as ON.
                "enabled": False,
            })

    return {
        "profile_id": str(board.get("session_profile_id") or board.get("port") or board.get("name") or "MB1"),
        "display_name": str(board.get("name", "MB1")),
        "last_known_port": board.get("port"),
        "device_type": "mp-Multiboard2",
        "vendor_id": None,
        "product_id": None,
        "serial_number": None,
        "last_known_firmware": board.get("firmware"),
        "pump_configuration": {"channels": channels},
        "valve_configuration": {"valves": []},
        "sensor_configuration": {
            "selected": [],
            "calibration": "water",
            "sample_rate_seconds": 1.0,
        },
    }


def _canonical_waveform(name: str) -> str | None:
    if name in WAVEFORM_CODES:
        return name
    alias = WAVEFORM_ALIASES.get(name)
    return alias if alias in WAVEFORM_CODES else None


def apply_board_profile(
    profile: dict[str, Any],
    board: dict[str, Any],
    *,
    available_waveform_ids: Iterable[str] = (),
) -> tuple[str, ...]:
    """Apply configuration to RAM only; return non-fatal warnings.

    ``available_waveform_ids`` is kept only for source compatibility with early
    older callers. Pump profiles no longer store or restore saved-Wave choices.
    """

    _ = available_waveform_ids
    warnings: list[str] = []
    saved_channels = {
        int(item.get("channel")): item
        for item in profile.get("pump_configuration", {}).get("channels", [])
        if isinstance(item, dict) and isinstance(item.get("channel"), int)
    }

    for driver in board.get("drivers", []):
        driver_index = int(driver.get("driver_index", 0))
        frequency_min, frequency_max = DRIVER_FREQUENCY_LIMITS[driver_index]
        driver_saved = next(
            (saved_channels.get(int(ch.get("channel"))) for ch in driver.get("channels", [])
             if saved_channels.get(int(ch.get("channel"))) is not None),
            None,
        )
        if driver_saved is not None:
            frequency = int(driver_saved.get("frequency_hz", driver.get("frequency", 100)))
            if frequency_min <= frequency <= frequency_max:
                driver["frequency"] = frequency
            else:
                warnings.append(
                    f"Ignored unsafe frequency {frequency} Hz for driver {driver_index}."
                )
            waveform = _canonical_waveform(str(driver_saved.get("waveform", driver.get("waveform", "Sinus"))))
            if waveform is not None:
                driver["waveform"] = waveform
            else:
                warnings.append(f"Ignored unsupported carrier waveform for driver {driver_index}.")

        amplitude_min, amplitude_max = DRIVER_AMPLITUDE_LIMITS[driver_index]
        for channel_data in driver.get("channels", []):
            channel = int(channel_data["channel"])
            saved = saved_channels.get(channel)
            if saved is None:
                channel_data["enabled"] = False
                continue
            amplitude = int(saved.get("amplitude_vpp", channel_data.get("amplitude", 100)))
            if amplitude_min <= amplitude <= amplitude_max:
                channel_data["amplitude"] = amplitude
            else:
                warnings.append(
                    f"Ignored unsafe CH{channel} amplitude {amplitude} Vpp; "
                    f"allowed {amplitude_min}–{amplitude_max} Vpp."
                )
            channel_data["enabled"] = False
            channel_data["hardware_state"] = "off"
    return tuple(warnings)


def find_profile_for_board(profiles: Iterable[dict[str, Any]], board: dict[str, Any]):
    """Match by saved port first, then by unique display name."""
    profiles = [profile for profile in profiles if isinstance(profile, dict)]
    port = board.get("port")
    if port:
        matches = [profile for profile in profiles if profile.get("last_known_port") == port]
        if len(matches) == 1:
            return matches[0]
    name = str(board.get("name", "")).casefold()
    matches = [
        profile for profile in profiles
        if str(profile.get("display_name", "")).casefold() == name and name
    ]
    return matches[0] if len(matches) == 1 else None
