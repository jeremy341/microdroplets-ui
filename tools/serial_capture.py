"""Record a safe Multiboard2 baseline transcript.

Usage:
    python tools/serial_capture.py COM5

The default sequence sends read-only identification/settings queries followed by
the documented global ``POFF`` safety command. ``POFF`` is intentionally
state-changing: it disables pumps, but it never starts a pump or sets amplitude or
frequency. For a strictly read-only driver fingerprint, use
``tools/driver_detection_probe.py`` instead.
"""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
import sys

import serial

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.application_paths import DIAGNOSTICS_DIR

from backend.raw_serial_capture import capture_commands, write_capture  # noqa: E402
BAUD_RATE = 115_200


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("port", help="COM port, e.g. COM5")
    parser.add_argument("--settle", type=float, default=0.6)
    parser.add_argument("--read-window", type=float, default=1.0)
    args = parser.parse_args()

    output = DIAGNOSTICS_DIR / f"serial_capture_{datetime.now():%Y%m%d_%H%M%S}.tsv"
    commands = ("V", "", "POFF")
    print(f"Opening {args.port} at {BAUD_RATE} 8N1")
    print("Safe sequence: V, Enter, POFF")
    try:
        with serial.Serial(
            port=args.port,
            baudrate=BAUD_RATE,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=0.1,
            write_timeout=1.0,
        ) as connection:
            reset = getattr(connection, "reset_input_buffer", None)
            if callable(reset):
                reset()
            records = capture_commands(
                connection,
                commands,
                settle_seconds=args.settle,
                read_window_seconds=args.read_window,
            )
    except serial.SerialException as exc:
        print(f"Serial error: {exc}", file=sys.stderr)
        return 2

    write_capture(output, records)
    print(f"Saved {len(records)} raw records to {output}")
    print("Send this .tsv back unchanged for protocol analysis.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
