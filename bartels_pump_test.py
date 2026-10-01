"""Safe, UI-independent terminal test for one MP-Multiboard2 pump channel.

The program never starts a pump automatically.  It opens the board at
115200 8N1, asks for the board/firmware response, sends POFF immediately,
configures one channel, and waits for explicit input before sending P<p>ON.
All TX/RX traffic is written to a uniquely named CSV in Documents.
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from datetime import datetime
from pathlib import Path

import serial
from serial.tools import list_ports


DEFAULT_BAUDRATE = 115200
DEFAULT_FREQUENCY_HZ = 100
DEFAULT_AMPLITUDE_VPP = 50
READ_TIMEOUT_SECONDS = 1.0


def documents_directory() -> Path:
    """Return the user's Windows Documents directory, with a local fallback."""
    if sys.platform == "win32":
        return Path.home() / "Documents"
    return Path(__file__).resolve().parent / "Documents"


def unique_log_path(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    candidate = directory / f"bartels_pump_test_{stamp}.csv"
    counter = 1
    while candidate.exists():
        candidate = directory / f"bartels_pump_test_{stamp}_{counter}.csv"
        counter += 1
    return candidate


class PumpTester:
    def __init__(self, connection: serial.Serial, log_path: Path) -> None:
        self.connection = connection
        self.log_file = log_path.open("w", newline="", encoding="utf-8")
        self.writer = csv.writer(self.log_file)
        self.writer.writerow(["timestamp", "direction", "line"])
        self.log_file.flush()

    def close(self) -> None:
        self.log_file.close()

    def send(self, command: str, *, wait: float = 0.25) -> list[str]:
        payload = (command + "\r\n").encode("ascii")
        timestamp = datetime.now().isoformat(timespec="milliseconds")
        self.writer.writerow([timestamp, "TX", command])
        self.log_file.flush()
        print(f">> {command}")
        self.connection.write(payload)
        self.connection.flush()
        time.sleep(wait)
        return self.read_available()

    def read_available(self) -> list[str]:
        received: list[str] = []
        while self.connection.in_waiting:
            raw = self.connection.readline()
            if not raw:
                break
            line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            received.append(line)
            timestamp = datetime.now().isoformat(timespec="milliseconds")
            self.writer.writerow([timestamp, "RX", line])
            self.log_file.flush()
            print(f"<< {line}")
        return received


def choose_port(requested: str | None) -> str:
    if requested:
        return requested
    ports = sorted(list_ports.comports(), key=lambda item: item.device)
    if not ports:
        raise RuntimeError("No serial ports found. Connect the MP-Multiboard2 first.")
    if len(ports) == 1:
        return ports[0].device
    print("Available serial ports:")
    for index, port in enumerate(ports, start=1):
        print(f"  {index}: {port.device} — {port.description}")
    choice = input("Select the board port: ").strip()
    try:
        return ports[int(choice) - 1].device
    except (ValueError, IndexError) as exc:
        raise RuntimeError("Invalid serial-port selection.") from exc


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", help="Serial port, for example COM3")
    parser.add_argument("--channel", type=int, choices=range(1, 7), default=1)
    parser.add_argument("--frequency", type=int, default=DEFAULT_FREQUENCY_HZ)
    parser.add_argument("--amplitude", type=int, default=DEFAULT_AMPLITUDE_VPP)
    parser.add_argument("--baudrate", type=int, default=DEFAULT_BAUDRATE)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.frequency <= 0 or args.amplitude <= 0:
        raise SystemExit("Frequency and amplitude must be positive.")

    port = choose_port(args.port)
    log_path = unique_log_path(documents_directory())
    connection: serial.Serial | None = None
    tester: PumpTester | None = None

    try:
        print(f"Connecting to {port} at {args.baudrate} 8N1 …")
        connection = serial.Serial(
            port=port,
            baudrate=args.baudrate,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=READ_TIMEOUT_SECONDS,
            write_timeout=READ_TIMEOUT_SECONDS,
        )
        tester = PumpTester(connection, log_path)
        print(f"Logging to: {log_path}")

        # V is the board/firmware identification query used by the flow test.
        tester.send("V")
        # Always place every pump in the safe state before configuring anything.
        tester.send("POFF")
        tester.send(f"F0={args.frequency}")
        tester.send(f"P{args.channel}V{args.amplitude}")

        print(
            f"Configured CH{args.channel}: {args.frequency} Hz, "
            f"{args.amplitude} Vpp."
        )
        input("Press Enter to start this channel … ")
        tester.send(f"P{args.channel}ON")
        input("Press Enter to stop this channel … ")
        tester.send(f"P{args.channel}OFF")
        print("Pump stopped.")
        return 0
    except KeyboardInterrupt:
        print("\nInterrupted; sending the emergency stop command.")
        return 130
    finally:
        if tester is not None:
            # This is intentionally repeated even after a normal channel stop.
            try:
                tester.send("POFF")
            except Exception as exc:
                print(f"Could not send final POFF: {exc}", file=sys.stderr)
            tester.close()
        if connection is not None:
            connection.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, serial.SerialException, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1)
