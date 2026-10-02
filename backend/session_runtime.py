"""Translate between live board dictionaries and safe session configuration.

No function in this module sends a command or opens a device.  It is therefore
safe to use during Save/Load without accidentally starting laboratory hardware.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Iterable

from backend.driver_capabilities import driver_index_for_channel
from backend.protocol import (
    DRIVER_AMPLITUDE_LIMITS,
    DRIVER_FREQUENCY_LIMITS,
    PUMP_CHANNELS,
    WAVEFORM_ALIASES,
    WAVEFORM_CODES,
)


DEFAULT_FREQUENCY_HZ = 100
DEFAULT_AMPLITUDE_VPP = 100
DEFAULT_WAVEFORM = "Sinus"
# Used only when the driver tables are empty, which no configured board does.
_FALLBACK_AMPLITUDE_LIMITS = (0, 250)
_FALLBACK_FREQUENCY_LIMITS = (0, 800)


def _limits_for_driver_index(
    table: dict[int, tuple[int, int]],
    driver_index: int | None,
    fallback: tuple[int, int],
) -> tuple[int, int]:
    """Return a driver's limits, widening them for an unknown driver index.

    A missing or unconfigured index (a board reporting an index the driver
    capabilities do not define) must never raise: the widest configured range
    is used so the caller keeps working and hardware is still forced safe.
    """

    limits = table.get(driver_index) if driver_index is not None else None
    if limits is not None:
        return limits
    if table:
        return min(low for low, _ in table.values()), max(high for _, high in table.values())
    return fallback


def _clamp_to_limits(value: Any, limits: tuple[int, int], default: int) -> int:
    """Return ``value`` as an int inside ``limits``, or ``default`` if unusable."""

    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(limits[0], min(number, limits[1]))


def _channel_number(channel: Any) -> int | None:
    """Return a session-saveable pump channel number, or None if unusable."""

    try:
        number = int(channel["channel"])
    except (TypeError, ValueError, KeyError, IndexError):
        return None
    return number if number in PUMP_CHANNELS else None


def _driver_index_for(declared_index: Any, channel_number: int) -> int:
    """Return a driver index the session schema accepts for one channel.

    The physical channel owns its driver, so a board reporting an unknown
    ``driver_index`` still yields a profile that ``validate_session`` accepts.
    """

    try:
        declared = int(declared_index)
    except (TypeError, ValueError):
        declared = -1
    if declared in DRIVER_FREQUENCY_LIMITS:
        return declared
    return driver_index_for_channel(channel_number)


def _canonical_waveform(name: str) -> str | None:
    if name in WAVEFORM_CODES:
        return name
    alias = WAVEFORM_ALIASES.get(name)
    return alias if alias in WAVEFORM_CODES else None


def build_board_profile(board: dict[str, Any]) -> dict[str, Any]:
    channels = []
    for driver in board.get("drivers", []):
        declared_index = driver.get("driver_index", 0)
        for channel in driver.get("channels", []):
            channel_number = _channel_number(channel)
            if channel_number is None:
                # A channel the schema cannot describe is dropped rather than
                # making the whole captured session unsaveable.
                continue
            driver_index = _driver_index_for(declared_index, channel_number)
            frequency_limits = _limits_for_driver_index(
                DRIVER_FREQUENCY_LIMITS, driver_index, _FALLBACK_FREQUENCY_LIMITS
            )
            amplitude_limits = _limits_for_driver_index(
                DRIVER_AMPLITUDE_LIMITS, driver_index, _FALLBACK_AMPLITUDE_LIMITS
            )
            channels.append({
                "channel": channel_number,
                "driver_index": driver_index,
                # Live values are clamped into the driver's limits so capture
                # can never emit a session that ``validate_session`` rejects.
                "frequency_hz": _clamp_to_limits(
                    driver.get("frequency"), frequency_limits, DEFAULT_FREQUENCY_HZ
                ),
                "amplitude_vpp": _clamp_to_limits(
                    channel.get("amplitude"), amplitude_limits, DEFAULT_AMPLITUDE_VPP
                ),
                "waveform": _canonical_waveform(str(driver.get("waveform", DEFAULT_WAVEFORM))) or DEFAULT_WAVEFORM,
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


def _declared_driver_index(driver: dict[str, Any]) -> int | None:
    """Return the driver's index, or None when the board reports a bad one."""

    try:
        return int(driver.get("driver_index", 0))
    except (TypeError, ValueError):
        return None


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
        driver_index = _declared_driver_index(driver)
        if driver_index is None or driver_index not in DRIVER_FREQUENCY_LIMITS:
            # An index the driver capabilities do not define has no limits of
            # its own. Warn and fall back to the widest configured range instead
            # of raising; channels are still forced off below.
            warnings.append(
                f"Unknown driver index {driver_index!r}; used the widest configured limits."
            )
            label = "unknown" if driver_index is None else driver_index
        else:
            label = driver_index
        frequency_min, frequency_max = _limits_for_driver_index(
            DRIVER_FREQUENCY_LIMITS, driver_index, _FALLBACK_FREQUENCY_LIMITS
        )
        amplitude_min, amplitude_max = _limits_for_driver_index(
            DRIVER_AMPLITUDE_LIMITS, driver_index, _FALLBACK_AMPLITUDE_LIMITS
        )
        driver_saved = next(
            (saved_channels.get(int(ch.get("channel"))) for ch in driver.get("channels", [])
             if saved_channels.get(int(ch.get("channel"))) is not None),
            None,
        )
        if driver_saved is not None:
            frequency = int(driver_saved.get("frequency_hz", driver.get("frequency", DEFAULT_FREQUENCY_HZ)))
            if frequency_min <= frequency <= frequency_max:
                driver["frequency"] = frequency
            else:
                warnings.append(
                    f"Ignored unsafe frequency {frequency} Hz for driver {label}."
                )
            waveform = _canonical_waveform(
                str(driver_saved.get("waveform", driver.get("waveform", DEFAULT_WAVEFORM)))
            )
            if waveform is not None:
                driver["waveform"] = waveform
            else:
                warnings.append(f"Ignored unsupported carrier waveform for driver {label}.")

        for channel_data in driver.get("channels", []):
            channel = int(channel_data["channel"])
            saved = saved_channels.get(channel)
            if saved is None:
                channel_data["enabled"] = False
                continue
            amplitude = int(saved.get("amplitude_vpp", channel_data.get("amplitude", DEFAULT_AMPLITUDE_VPP)))
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
