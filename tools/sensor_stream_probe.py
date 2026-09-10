"""Probe the Multiboard2 liquid-flow stream safely.

Usage:
    python tools/sensor_stream_probe.py COM5

Sequence: DFOFF, L0, DFON, ten seconds of raw capture, DFOFF, two-second
post-stop check. No pump is started.
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

from backend.raw_serial_capture import write_capture  # noqa: E402
from backend.sensor_stream_capture import analyze_sensor_stream, capture_sensor_stream  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("port", help="COM port, e.g. COM5")
    parser.add_argument("--seconds", type=float, default=10.0,
                        help="seconds to observe the stream (default: 10)")
    parser.add_argument("--post-stop", type=float, default=2.0,
                        help="seconds to observe after DFOFF (default: 2)")
    args = parser.parse_args()
    output = DIAGNOSTICS_DIR / f"sensor_stream_{datetime.now():%Y%m%d_%H%M%S}.tsv"
    print(f"Opening {args.port} at 115200 8N1")
    print("Safe sequence: DFOFF, L0, DFON, capture, DFOFF")
    try:
        with serial.Serial(args.port, 115200, bytesize=serial.EIGHTBITS,
                           parity=serial.PARITY_NONE, stopbits=serial.STOPBITS_ONE,
                           timeout=0.1, write_timeout=1.0) as connection:
            reset = getattr(connection, "reset_input_buffer", None)
            if callable(reset):
                reset()
            records = capture_sensor_stream(connection, args.seconds, post_stop_seconds=args.post_stop)
    except serial.SerialException as exc:
        print(f"Serial error: {exc}", file=sys.stderr)
        return 2
    write_capture(output, records)
    result = analyze_sensor_stream(records)
    print(f"Saved raw capture to {output}")
    print(f"DFON acknowledged: {'yes' if result.start_ack else 'no/unclear'}")
    print(f"Recognized measurement lines: {len(result.measurement_lines)}")
    print(f"Measurement format: {result.measurement_format}")
    print(f"DFOFF acknowledged: {'yes' if result.stop_ack else 'no/unclear'}")
    print(f"Bytes after stop: {result.post_stop_bytes}")
    print(f"Stream stopped cleanly: {'yes' if result.stopped_cleanly else 'no/unclear'}")
    print("Send the TSV back unchanged for final protocol integration.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
