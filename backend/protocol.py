"""Bartels Multiboard2 command builders and strict response parsing.

The Multiboard firmware sends newline-delimited text.  A plain ``V=`` value is
associated with the one stream currently active on that board.  Named markers
are also accepted for firmware versions that identify the sensor explicitly.

This module is the single source of truth for command syntax used by normal
pump control. Driver/channel capabilities come from ``driver_capabilities``.
See ``docs/DEVELOPER_GUIDE.md`` before adding or changing board commands.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from enum import Enum

from backend.driver_capabilities import (
    DRIVER_CHANNELS,
    capabilities_for_driver_index,
    configured_amplitude_limits,
    configured_frequency_limits,
)


COMMAND_TERMINATOR = b"\r\n"


class Calibration(str, Enum):
    WATER = "water"
    IPA = "ipa"


@dataclass(frozen=True)
class SensorDefinition:
    sensor_id: str
    label: str
    unit: str
    start_command: str
    stop_command: str
    response_markers: tuple[str, ...] = ()
    firmware_dependent: bool = False


SENSORS: dict[str, SensorDefinition] = {
    "liquid_flow": SensorDefinition(
        "liquid_flow", "Liquid Flow Rate", "µL/min", "DFON", "DFOFF", ("RSLF",)
    ),
    "pressure": SensorDefinition(
        "pressure", "Pressure", "mbar", "DPON", "DPOFF", ("RSDPC",)
    ),
    "gas_flow": SensorDefinition(
        "gas_flow", "Gas Flow Rate", "mL/min", "DGON", "DGOFF"
    ),
    "analog_1": SensorDefinition("analog_1", "Analog 1", "raw", "DA1ON", "DA1OFF"),
    "analog_2": SensorDefinition("analog_2", "Analog 2", "raw", "DA2ON", "DA2OFF"),
    "analog_3": SensorDefinition("analog_3", "Analog 3", "raw", "DA3ON", "DA3OFF"),
    "thermal_conductivity": SensorDefinition(
        "thermal_conductivity", "Thermal Conductivity", "raw", "DCON", "DCOFF",
        firmware_dependent=True,
    ),
    "co2": SensorDefinition(
        "co2", "CO2", "ppm", "DCO2ON", "DCO2OFF", ("CO2",), True
    ),
    "voc": SensorDefinition(
        "voc", "VOC", "raw", "DVOCON", "DVOCOFF", ("VOC",), True
    ),
}

CALIBRATION_COMMANDS = {Calibration.WATER: "L0", Calibration.IPA: "L1"}

# Pump-control mappings from Bartels Software Manual v1.6, section 2.1.6.
# Driver 0 owns CH1-CH4, driver 1 owns CH5, and driver 2 owns CH6.  The
# physical CH5 driver type is supplied by data/driver_config.json.
PUMP_CHANNELS = range(1, 7)
PUMP_DRIVER_CHANNELS = DRIVER_CHANNELS
DRIVER_FREQUENCY_LIMITS = configured_frequency_limits()
DRIVER_AMPLITUDE_LIMITS = configured_amplitude_limits()
WAVEFORM_CODES = {
    "Sinus": 0,
    "Sinus-Like": 1,
    "Rect-Like": 2,
    "Rect.": 3,
}
WAVEFORM_ALIASES = {
    "Sine-rectangular 1": "Sinus-Like",
    "Sine-rectangular 2": "Rect-Like",
    "Rectangular": "Rect.",
}


@dataclass(frozen=True)
class ParsedMeasurement:
    sensor_id: str
    value: float
    unit: str
    raw_line: str
    raw_value_ml_min: float | None = None


@dataclass(frozen=True)
class ParsedReply:
    kind: str
    raw_line: str
    measurement: ParsedMeasurement | None = None
    message: str = ""


_NUMBER = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
# Some firmware builds append the physical unit to a measurement line
# ("V=1.25 mL/min"). Only the closed set of units the board can report is
# accepted as a suffix, so a line that merely ends in a word ("V=1.2 extra")
# stays unparsed instead of being guessed at.
_KNOWN_UNIT = (
    r"(?:[mµμu]?[lL]|L)\s*/\s*(?:min|s)|mbar|bar|kpa|ppm|ppb|%|\braw\b"
)
_VALUE_PATTERN = re.compile(
    rf"^V\s*=\s*({_NUMBER})(?:\s*({_KNOWN_UNIT})\s*)?$", re.IGNORECASE
)
_MARKED_PATTERN = re.compile(
    rf"^(RSLF|RSDPC|CO2|VOC)\s*(?:=|:|,|;|\s)\s*({_NUMBER})(?:\s*({_KNOWN_UNIT})\s*)?$",
    re.IGNORECASE,
)
_MARKER_TO_SENSOR = {
    "RSLF": "liquid_flow",
    "RSDPC": "pressure",
    "CO2": "co2",
    "VOC": "voc",
}


def encode_command(command: str) -> bytes:
    """Encode exactly one ASCII command with the required CRLF terminator."""

    clean = command.strip()
    if not clean or "\r" in clean or "\n" in clean:
        raise ValueError("Command must be one non-empty line")
    return clean.encode("ascii") + COMMAND_TERMINATOR


def pump_state_command(channel: int, enabled: bool) -> str:
    """Build a documented per-channel pump ON/OFF command."""

    if type(channel) is not int or channel not in PUMP_CHANNELS:
        raise ValueError("Pump channel must be between 1 and 6")
    if type(enabled) is not bool:
        raise ValueError("Pump state must be a boolean")
    return f"P{channel}{'ON' if enabled else 'OFF'}"


def pump_amplitude_command(channel: int, amplitude_vpp: int) -> str:
    """Build a documented pump amplitude command in peak-to-peak volts."""

    if type(channel) is not int or channel not in PUMP_CHANNELS:
        raise ValueError("Pump channel must be between 1 and 6")
    if type(amplitude_vpp) is not int or not 0 <= amplitude_vpp <= 250:
        raise ValueError("Pump amplitude must be between 0 and 250 Vpp")
    return f"P{channel}V{amplitude_vpp}"


def driver_amplitude_command(
    driver_index: int, channel: int, amplitude_vpp: int
) -> str:
    """Build an amplitude command after applying the selected driver's limits."""

    if driver_index not in DRIVER_AMPLITUDE_LIMITS:
        raise ValueError("Driver index must be 0, 1, or 2")
    if channel not in PUMP_DRIVER_CHANNELS[driver_index]:
        raise ValueError(f"Channel {channel} does not belong to driver {driver_index}")
    minimum, maximum = DRIVER_AMPLITUDE_LIMITS[driver_index]
    if type(amplitude_vpp) is not int or not minimum <= amplitude_vpp <= maximum:
        raise ValueError(
            f"Driver {driver_index} amplitude must be between {minimum} and {maximum} Vpp"
        )
    return pump_amplitude_command(channel, amplitude_vpp)


def driver_frequency_command(driver_index: int, frequency_hz: int) -> str:
    """Build the documented shared-frequency command for one driver group."""

    if type(driver_index) is not int or driver_index not in PUMP_DRIVER_CHANNELS:
        raise ValueError("Driver index must be 0, 1, or 2")
    minimum, maximum = DRIVER_FREQUENCY_LIMITS[driver_index]
    if type(frequency_hz) is not int or not minimum <= frequency_hz <= maximum:
        raise ValueError(
            f"Driver {driver_index} frequency must be between {minimum} and {maximum} Hz"
        )
    return f"F{driver_index}={frequency_hz}"


def driver_waveform_command(driver_index: int, waveform: str) -> str:
    """Build a carrier-shape command only for drivers that support CS<d>."""

    capabilities = capabilities_for_driver_index(driver_index)
    if not capabilities.supports_carrier_waveform:
        raise ValueError(
            f"Signal shape is not supported for {capabilities.display_name} "
            f"on driver {driver_index}"
        )
    try:
        waveform_code = WAVEFORM_CODES.get(
            WAVEFORM_ALIASES.get(waveform, waveform)
        )
        if waveform_code is None:
            raise KeyError(waveform)
    except KeyError as exc:
        raise ValueError(f"Unsupported waveform: {waveform}") from exc
    return f"CS{driver_index}={waveform_code}"


def pump_start_commands(
    driver_index: int,
    frequency_hz: int,
    waveform: str,
    channel: int,
    amplitude_vpp: int,
) -> tuple[str, ...]:
    """Return the deterministic configure-then-start sequence used by the UI."""

    if channel not in PUMP_DRIVER_CHANNELS.get(driver_index, ()):
        raise ValueError(f"Channel {channel} does not belong to driver {driver_index}")
    commands = [driver_frequency_command(driver_index, frequency_hz)]
    if capabilities_for_driver_index(driver_index).supports_carrier_waveform:
        commands.append(driver_waveform_command(driver_index, waveform))
    commands.extend(
        (
            driver_amplitude_command(driver_index, channel, amplitude_vpp),
            pump_state_command(channel, True),
        )
    )
    return tuple(commands)


def parse_reply(line: str, active_sensor_id: str | None = None) -> ParsedReply:
    """Parse one complete reply without guessing numbers from unknown lines."""

    clean = line.strip()
    if not clean:
        return ParsedReply("empty", line)
    # The board commonly prefixes console replies with ``<<``.
    normalized = re.sub(r"^<+\s*", "", clean).strip()
    if normalized.upper() == "OK":
        return ParsedReply("ack", clean)
    if normalized.lower().startswith("multiboard") and normalized.lower().endswith(" ready"):
        return ParsedReply("boot", clean, message=normalized)
    if clean.startswith("[E][Wire.cpp:") and "i2c" in clean.lower():
        # ESP32 Wire diagnostics describe a sensor-bus condition; they are not
        # Multiboard command rejections and must remain visible as diagnostics.
        return ParsedReply("diagnostic", clean, message=clean)
    if clean.upper().startswith(("FAIL", "ERR", "ERROR", "WRONG COMMAND")):
        return ParsedReply("error", clean, message=clean)
    if normalized.lower().startswith("multiboard"):
        return ParsedReply("firmware", clean, message=normalized)

    sensor_id: str | None = None
    value_text: str | None = None
    # Match against the prefix-stripped line: a measurement must parse the
    # same way whether or not the firmware used the console "<<" marker.
    match = _MARKED_PATTERN.fullmatch(normalized)
    if match:
        sensor_id = _MARKER_TO_SENSOR[match.group(1).upper()]
        value_text = match.group(2)
    else:
        match = _VALUE_PATTERN.fullmatch(normalized)
        if match and active_sensor_id in SENSORS:
            sensor_id = active_sensor_id
            value_text = match.group(1)

    if sensor_id is None or value_text is None:
        return ParsedReply("unknown", clean, message=clean)

    raw_value = float(value_text)
    if not math.isfinite(raw_value):
        return ParsedReply("error", clean, message="Non-finite measurement")
    definition = SENSORS[sensor_id]
    # Firmware sends liquid flow numerically in mL/min; normalize once at the
    # protocol boundary to FluidicStudio's displayed µL/min unit.
    value = raw_value * 1000.0 if sensor_id == "liquid_flow" else raw_value
    return ParsedReply(
        "measurement",
        clean,
        ParsedMeasurement(
            sensor_id,
            value,
            definition.unit,
            clean,
            raw_value if sensor_id == "liquid_flow" else None,
        ),
    )
