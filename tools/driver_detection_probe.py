"""Read-only Bartels mp-Multiboard2 driver-detection fingerprint probe.

This tool is for hardware reconnaissance, not production driver selection.
It deliberately sends only documented read-only queries:

* ``V`` to read the firmware/version response.
* a blank CRLF (Enter) to request the current Multiboard settings.
* optionally ``P1V?`` .. ``P6V?`` to read stored channel amplitudes.

It never sends pump ON/OFF, amplitude-setting, frequency-setting, signal-shape,
sensor-start, or valve commands.  The ``Driver:`` field returned by some
firmware versions is preserved as an opaque *fingerprint*.  Bartels documents
that driver detection exists in its Multiboard2 software, but the serial manual
does not document the encoding of that field, so this tool never guesses that
``4``, ``D`` or any other token means a specific driver.

See ``docs/DEVELOPER_GUIDE.md`` before interpreting a capture.

Examples::

    python tools/driver_detection_probe.py COM3 --label highdriver4_only
    python tools/driver_detection_probe.py COM3 --label mp_driver_only --amplitude-queries
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.application_paths import DIAGNOSTICS_DIR


BAUD_RATE = 115_200
_DRIVER_RE = re.compile(r"^\s*Driver\s*:\s*(.*?)\s*$", re.IGNORECASE)
_FREQUENCY_RE = re.compile(r"^\s*Frequency(\d+)\s*:\s*(\d+)\s*Hz\s*$", re.IGNORECASE)
_PUMP_RE = re.compile(r"^\s*Pump(\d+)\s*:\s*(.*?)\s*$", re.IGNORECASE)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _safe_label(value: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip())
    return clean.strip("._-") or "unlabelled"


def parse_settings_lines(lines: list[str]) -> dict[str, Any]:
    """Extract known fields from a settings dump without decoding drivers.

    The raw ``Driver:`` payload is intentionally returned unchanged as
    ``driver_fingerprint``.  Frequency and Pump lines are settings/state
    evidence only and are not treated as proof that a physical driver or pump
    is installed.
    """

    driver_fingerprint: str | None = None
    frequencies_hz: dict[str, int] = {}
    pump_settings: dict[str, str] = {}
    unknown_lines: list[str] = []

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue

        match = _DRIVER_RE.fullmatch(line)
        if match:
            driver_fingerprint = match.group(1).strip() or None
            continue

        match = _FREQUENCY_RE.fullmatch(line)
        if match:
            frequencies_hz[match.group(1)] = int(match.group(2))
            continue

        match = _PUMP_RE.fullmatch(line)
        if match:
            pump_settings[match.group(1)] = match.group(2).strip()
            continue

        unknown_lines.append(line)

    return {
        "driver_fingerprint": driver_fingerprint,
        "frequencies_hz": frequencies_hz,
        "pump_settings": pump_settings,
        "unknown_settings_lines": unknown_lines,
    }


def _read_lines(connection: Any, seconds: float) -> list[str]:
    lines: list[str] = []
    deadline = time.monotonic() + max(0.05, float(seconds))
    while time.monotonic() < deadline:
        raw = connection.readline()
        if not raw:
            continue
        text = raw.decode("utf-8", errors="replace").rstrip("\r\n")
        if text:
            lines.append(text)
    return lines


def _query(connection: Any, command: str, read_window: float) -> list[str]:
    label = command if command else "<ENTER / settings>"
    payload = b"\r\n" if command == "" else (command + "\r\n").encode("ascii")
    print(f"TX: {label}")
    connection.write(payload)
    connection.flush()
    lines = _read_lines(connection, read_window)
    if lines:
        for line in lines:
            print(f"  RX: {line}")
    else:
        print("  RX: <no response>")
    return lines


def build_report(
    *,
    port: str,
    label: str,
    firmware_lines: list[str],
    settings_lines: list[str],
    amplitude_replies: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """Build the JSON report used for empirical fingerprint comparison."""

    parsed = parse_settings_lines(settings_lines)
    fingerprint = parsed["driver_fingerprint"]
    return {
        "schema_version": 1,
        "timestamp_utc": _utc_now(),
        "label": label,
        "port": port,
        "transport": {
            "baud_rate": BAUD_RATE,
            "data_bits": 8,
            "parity": "none",
            "stop_bits": 1,
            "terminator": "\\r\\n",
        },
        "firmware_lines": firmware_lines,
        "settings_lines": settings_lines,
        "observations": parsed,
        "amplitude_query_replies": amplitude_replies or {},
        "interpretation": {
            "driver_detection_evidence_present": fingerprint is not None,
            "driver_fingerprint_status": "opaque_firmware_fingerprint" if fingerprint else "not_reported",
            "mapping_status": "unverified",
            "physical_pump_count": "unknown",
            "note": (
                "Do not decode the Driver field from this capture alone. "
                "Build a mapping only from repeated captures with known, labelled hardware configurations."
            ),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("port", help="Multiboard COM port, e.g. COM3")
    parser.add_argument(
        "--label",
        default="unlabelled",
        help="Physical hardware configuration, e.g. highdriver4_only",
    )
    parser.add_argument(
        "--amplitude-queries",
        action="store_true",
        help="Also send documented read-only P1V? .. P6V? queries",
    )
    parser.add_argument("--read-window", type=float, default=1.0)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DIAGNOSTICS_DIR,
    )
    args = parser.parse_args()

    label = _safe_label(args.label)
    if label == "unlabelled":
        print("WARNING: use --label with the physically installed driver configuration")
        print("         if this capture will be used to build a fingerprint mapping.\n")

    print(f"Opening {args.port} at {BAUD_RATE} 8N1")
    print("Read-only sequence: V, Enter/settings" + (", P1V?..P6V?" if args.amplitude_queries else ""))
    print("No driver mapping will be guessed from the returned Driver field.\n")

    try:
        import serial
    except ImportError:
        print("pyserial is required to access the Multiboard serial port.")
        print("Install the project requirements before running this hardware probe.")
        return 3

    try:
        with serial.Serial(
            port=args.port,
            baudrate=BAUD_RATE,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=0.12,
            write_timeout=1.0,
        ) as connection:
            reset = getattr(connection, "reset_input_buffer", None)
            if callable(reset):
                reset()

            firmware_lines = _query(connection, "V", args.read_window)
            settings_lines = _query(connection, "", args.read_window)

            amplitude_replies: dict[str, list[str]] = {}
            if args.amplitude_queries:
                for channel in range(1, 7):
                    command = f"P{channel}V?"
                    amplitude_replies[str(channel)] = _query(
                        connection, command, args.read_window
                    )
    except serial.SerialException as exc:
        print(f"Serial error: {exc}")
        print("Close FluidicStudio/Bartels software if it currently owns the COM port.")
        return 2

    report = build_report(
        port=args.port,
        label=label,
        firmware_lines=firmware_lines,
        settings_lines=settings_lines,
        amplitude_replies=amplitude_replies,
    )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output = args.output_dir / f"driver_probe_{stamp}_{label}.json"
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    fingerprint = report["observations"]["driver_fingerprint"]
    print("\nResult")
    print(f"  label:              {label}")
    print(f"  driver fingerprint: {fingerprint if fingerprint is not None else '<not reported>'}")
    print(f"  saved:              {output}")
    print("\nInterpret the fingerprint only after comparing labelled captures from known hardware.")
    print("Power the board OFF before inserting/removing any pump driver.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
