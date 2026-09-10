"""Probe the Multiboard2 liquid-flow stream with live terminal output.

Usage:
    python tools/sensor_stream_probe_live.py COM5

The safe sequence is DFOFF, L0, DFON, live capture, DFOFF and a post-stop
check. No pump command is sent.
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
from backend.sensor_stream_capture import analyze_sensor_stream  # noqa: E402
from backend.sensor_stream_capture_live import (  # noqa: E402
    capture_sensor_stream_live,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("port", help="COM port, e.g. COM5")
    parser.add_argument("--seconds", type=float, default=10.0,
                        help="seconds to observe the stream (default: 10)")
    parser.add_argument("--post-stop", type=float, default=2.0,
                        help="seconds to observe after DFOFF (default: 2)")
    args = parser.parse_args()

    if args.seconds <= 0 or args.post_stop < 0:
        parser.error("--seconds must be > 0 and --post-stop must be >= 0")

    output = DIAGNOSTICS_DIR / (
        f"sensor_stream_live_{datetime.now():%Y%m%d_%H%M%S}.tsv"
    )
    line_buffer = ""
    measurement_count = 0
    measurement_started = False

    print(f"Opening {args.port} at 115200 8N1", flush=True)
    print("Safe sequence: DFOFF, L0, DFON, capture, DFOFF", flush=True)
    print("No pump command will be sent.", flush=True)

    def show_record(record) -> None:
        nonlocal line_buffer, measurement_count, measurement_started
        if record.direction == "TX":
            command = record.payload_text.strip().upper()
            if command == "DFON":
                measurement_started = True
                print("\nDFON sent. Measurement phase starts now.", flush=True)
                print("Move the syringe slowly and steadily through the sensor now.", flush=True)
                print(f"Live readings for {args.seconds:g} seconds:", flush=True)
            elif command == "DFOFF" and measurement_started:
                print("\nDFOFF sent. Stop moving the syringe.", flush=True)
                print(f"Checking for late data for {args.post_stop:g} seconds...", flush=True)
            return

        if not measurement_started:
            return
        line_buffer += record.payload_text
        parts = line_buffer.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        line_buffer = parts.pop()
        for line in parts:
            line = line.strip()
            if line.upper().startswith("V="):
                measurement_count += 1
                print(f"  [{measurement_count:03d}] {line}", flush=True)

    try:
        with serial.Serial(args.port, 115200, bytesize=serial.EIGHTBITS,
                           parity=serial.PARITY_NONE, stopbits=serial.STOPBITS_ONE,
                           timeout=0.1, write_timeout=1.0) as connection:
            reset = getattr(connection, "reset_input_buffer", None)
            if callable(reset):
                reset()
            records = capture_sensor_stream_live(
                connection, args.seconds, post_stop_seconds=args.post_stop,
                on_record=show_record,
            )
    except serial.SerialException as exc:
        print(f"Serial error: {exc}", file=sys.stderr)
        return 2

    write_capture(output, records)
    result = analyze_sensor_stream(records)
    l0_ack = False
    l0_error = False
    l0_seen = False
    for index, record in enumerate(records):
        if record.direction != "TX" or record.payload_text.strip().upper() != "L0":
            continue
        l0_seen = True
        for following in records[index + 1:]:
            if following.direction == "TX":
                break
            if "OK" in following.payload_text.upper():
                l0_ack = True
                break
            if "WRONG COMMAND" in following.payload_text.upper() or "ERROR" in following.payload_text.upper():
                l0_error = True
                break
    print("\n--- Test result ---", flush=True)
    print(f"Saved raw capture to {output}")
    print(f"L0 sent as CRLF command: {'yes' if l0_seen else 'no'}")
    if l0_ack:
        l0_status = "OK"
    elif l0_error:
        l0_status = "board reported command error"
    else:
        l0_status = "no reply (allowed by Bartels protocol)"
    print(f"L0 response: {l0_status}")
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
