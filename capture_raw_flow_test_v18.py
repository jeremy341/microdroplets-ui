"""Capture an unfiltered Multiboard2/SLF3S-1300F flow test.

This script records the exact TX/RX serial transcript. It does not apply an
EMA, median filter, deadband, unit conversion, or volume integration.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from time import monotonic

try:
    import serial
    from serial.tools import list_ports
except ModuleNotFoundError:
    serial = None  # type: ignore[assignment]
    list_ports = None  # type: ignore[assignment]

from backend.diagnostics import (
    RawSerialCapture,
    TranscriptEntry,
    TranscriptWriter,
    open_multiboard,
    wait_with_error_check,
)


def available_ports() -> list:
    if list_ports is None:
        raise RuntimeError(
            "pyserial is not installed. Run: python -m pip install -r requirements.txt"
        )
    return sorted(list_ports.comports(), key=lambda item: item.device)


def choose_port(requested: str | None) -> str:
    ports = available_ports()

    if requested:
        known = {item.device.casefold() for item in ports}
        if requested.casefold() not in known:
            print(f"Warning: {requested} is not currently detected.")
        return requested

    if not ports:
        raise RuntimeError(
            "No COM port found. Check the USB cable and CP210x driver in Device Manager."
        )

    print("Detected serial ports:")
    for index, item in enumerate(ports, start=1):
        details = " - ".join(part for part in (item.description, item.hwid) if part)
        print(f"  {index}. {item.device}: {details}")

    if len(ports) == 1:
        return ports[0].device

    while True:
        answer = input("Select port number: ").strip()
        try:
            return ports[int(answer) - 1].device
        except (ValueError, IndexError):
            print(f"Enter a number from 1 to {len(ports)}.")


def default_output_path() -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return Path("diagnostics") / f"raw_flow_{stamp}.csv"


def print_entry(entry: TranscriptEntry) -> None:
    prefix = "-->" if entry.direction == "TX" else "<--"
    print(f"{prefix} [{entry.elapsed_seconds:8.3f}s] {entry.payload}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Capture an unfiltered raw Multiboard2 liquid-flow transcript."
    )
    parser.add_argument("--port", help="COM port, for example COM5")
    parser.add_argument(
        "--output",
        type=Path,
        default=default_output_path(),
        help="CSV transcript path",
    )
    parser.add_argument(
        "--zero-seconds",
        type=float,
        default=10.0,
        help="Stationary baseline duration before the manual flow starts",
    )
    parser.add_argument(
        "--flow-seconds",
        type=float,
        default=30.0,
        help="Fixed raw-flow recording duration after pressing Enter",
    )
    parser.add_argument(
        "--post-seconds",
        type=float,
        default=3.0,
        help="Recording time after DFOFF before closing the transcript",
    )
    return parser.parse_args()


def run() -> int:
    args = parse_args()
    if args.zero_seconds < 0 or args.flow_seconds <= 0 or args.post_seconds < 0:
        raise ValueError("zero-seconds/post-seconds must be >= 0 and flow-seconds must be > 0")

    connection = None
    transcript = None
    capture = None
    flow_started = False

    print("Multiboard2 / SLF3S-1300F RAW FLOW TEST")
    print("No EMA, median filter, deadband, scaling, or volume integration is used.")
    print("Close FluidicStudio before continuing.")
    print("Use clean water, follow the sensor arrow, and push the syringe evenly.")

    try:
        port = choose_port(args.port)
        input(f"Press Enter when FluidicStudio is closed and {port} is ready... ")

        connection = open_multiboard(port)
        started_at = monotonic()
        transcript = TranscriptWriter(args.output.resolve(), started_at)
        capture = RawSerialCapture(connection, transcript, print_entry)
        capture.start()

        wait_with_error_check(capture, 1.0)
        capture.send("V")
        wait_with_error_check(capture, 1.0)
        capture.send("L0")
        capture.wait_for_payload("OK", timeout=3.0)
        capture.send("DFON")
        # Do not call the test a recording when the board rejected DFON.  The
        # previous version ignored FAIL and spent 30 seconds recording no V=
        # samples, which produced a misleading "Capture complete" message.
        capture.wait_for_payload("OK", timeout=3.0)
        flow_started = True

        print(f"Recording stationary baseline for {args.zero_seconds:g} seconds...")
        wait_with_error_check(capture, args.zero_seconds)

        input(
            "Press Enter and immediately push the syringe evenly. "
            f"Recording then runs for {args.flow_seconds:g} seconds: "
        )
        print("RAW FLOW RECORDING STARTED")
        wait_with_error_check(capture, args.flow_seconds)

        capture.send("DFOFF")
        capture.wait_for_payload("OK", timeout=3.0)
        flow_started = False
        wait_with_error_check(capture, args.post_seconds)
        capture.raise_reader_error()

        print(f"Capture complete: {args.output.resolve()}")
        return 0
    except KeyboardInterrupt:
        print("\nInterrupted; the finally block will stop the flow stream safely.")
        return 130
    except Exception as exc:
        if serial is not None and isinstance(exc, serial.SerialException):
            print(
                "Serial error. Check that FluidicStudio is closed, the COM port is "
                "correct, and the CP210x device has no warning in Device Manager.",
                file=sys.stderr,
            )
            return 2
        if isinstance(exc, (OSError, RuntimeError, ValueError)):
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        raise
    finally:
        if capture is not None and connection is not None and connection.is_open:
            if flow_started:
                try:
                    capture.send("DFOFF")
                except Exception:
                    pass
            capture.stop()
        elif transcript is not None:
            transcript.close()
        if connection is not None and connection.is_open:
            connection.close()


if __name__ == "__main__":
    raise SystemExit(run())
