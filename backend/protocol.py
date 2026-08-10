"""Bartels Multiboard2 sensor commands and strict response parsing.

The Multiboard firmware sends newline-delimited text.  A plain ``V=`` value is
associated with the one stream currently active on that board.  Named markers
are also accepted for firmware versions that identify the sensor explicitly.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from enum import Enum


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
_VALUE_PATTERN = re.compile(rf"^V\s*=\s*({_NUMBER})\s*$", re.IGNORECASE)
_MARKED_PATTERN = re.compile(
    rf"^(RSLF|RSDPC|CO2|VOC)\s*(?:=|:|,|;|\s)\s*({_NUMBER})\s*$",
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


def parse_reply(line: str, active_sensor_id: str | None = None) -> ParsedReply:
    """Parse one complete reply without guessing numbers from unknown lines."""

    clean = line.strip()
    if not clean:
        return ParsedReply("empty", line)
    if clean.upper() == "OK":
        return ParsedReply("ack", clean)
    if clean.upper().startswith(("ERR", "ERROR")):
        return ParsedReply("error", clean, message=clean)
    if clean.lower().startswith("multiboard"):
        return ParsedReply("firmware", clean, message=clean)

    sensor_id: str | None = None
    value_text: str | None = None
    match = _MARKED_PATTERN.fullmatch(clean)
    if match:
        sensor_id = _MARKER_TO_SENSOR[match.group(1).upper()]
        value_text = match.group(2)
    else:
        match = _VALUE_PATTERN.fullmatch(clean)
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
