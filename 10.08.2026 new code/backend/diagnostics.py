"""Safe raw serial diagnostics for the Bartels mp-Multiboard2.

This module intentionally does not interpret sensor values.  Its purpose is to
capture the exact serial transcript emitted by the firmware so production
parsers can be built from real data instead of guessed response formats.
"""

from __future__ import annotations

import csv
import queue
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic, sleep
from typing import Any, Callable, Protocol

try:
    import serial
except ModuleNotFoundError:  # Allows transport-independent unit tests to run.
    serial = None  # type: ignore[assignment]


BAUD_RATE = 115_200
COMMAND_TERMINATOR = b"\r\n"


class SerialLike(Protocol):
    """Small interface used by :class:`RawSerialCapture` and its tests."""

    is_open: bool

    def read(self, size: int = 1) -> bytes: ...

    def write(self, data: bytes) -> int: ...

    def flush(self) -> None: ...

    def close(self) -> None: ...


@dataclass(frozen=True)
class TranscriptEntry:
    timestamp_utc: str
    elapsed_seconds: float
    direction: str
    payload: str
    payload_hex: str


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def encode_command(command: str) -> bytes:
    """Validate and encode exactly one Bartels command."""

    clean = command.strip()
    if not clean:
        raise ValueError("Command must not be empty")
    if "\r" in clean or "\n" in clean:
        raise ValueError("Command must not contain line breaks")
    return clean.encode("ascii") + COMMAND_TERMINATOR


class TranscriptWriter:
    """Thread-safe CSV transcript writer that flushes every entry."""

    HEADER = (
        "timestamp_utc",
        "elapsed_seconds",
        "direction",
        "payload",
        "payload_hex",
    )

    def __init__(self, path: Path, started_at: float) -> None:
        self.path = path
        self._started_at = started_at
        self._lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)
        self._file = path.open("w", newline="", encoding="utf-8")
        self._writer = csv.writer(self._file)
        self._writer.writerow(self.HEADER)
        self._file.flush()

    def append(self, direction: str, raw: bytes) -> TranscriptEntry:
        entry = TranscriptEntry(
            timestamp_utc=utc_timestamp(),
            elapsed_seconds=monotonic() - self._started_at,
            direction=direction,
            payload=raw.decode("utf-8", errors="backslashreplace").rstrip("\r\n"),
            payload_hex=raw.hex(" "),
        )
        with self._lock:
            self._writer.writerow(
                (
                    entry.timestamp_utc,
                    f"{entry.elapsed_seconds:.6f}",
                    entry.direction,
                    entry.payload,
                    entry.payload_hex,
                )
            )
            self._file.flush()
        return entry

    def close(self) -> None:
        with self._lock:
            if not self._file.closed:
                self._file.flush()
                self._file.close()


class RawSerialCapture:
    """Read raw serial data in a worker thread and record all TX/RX traffic."""

    def __init__(
        self,
        connection: SerialLike,
        transcript: TranscriptWriter,
        on_entry: Callable[[TranscriptEntry], None] | None = None,
    ) -> None:
        self.connection = connection
        self.transcript = transcript
        self.on_entry = on_entry
        self.errors: queue.Queue[BaseException] = queue.Queue()
        self.entries: queue.Queue[TranscriptEntry] = queue.Queue()
        self._stop_event = threading.Event()
        self._reader = threading.Thread(
            target=self._reader_loop,
            name="multiboard-raw-reader",
            daemon=True,
        )

    def start(self) -> None:
        self._reader.start()

    def send(self, command: str) -> None:
        raw = encode_command(command)
        self.connection.write(raw)
        self.connection.flush()
        self._publish(self.transcript.append("TX", raw))

    def _reader_loop(self) -> None:
        buffer = bytearray()
        try:
            while not self._stop_event.is_set():
                chunk = self.connection.read(256)
                if not chunk:
                    continue
                buffer.extend(chunk)
                while b"\n" in buffer:
                    end = buffer.index(b"\n") + 1
                    raw_line = bytes(buffer[:end])
                    del buffer[:end]
                    self._publish(self.transcript.append("RX", raw_line))
        except BaseException as exc:  # passed to the controlling/main thread
            self.errors.put(exc)
            self._stop_event.set()
        finally:
            if buffer:
                self._publish(self.transcript.append("RX_PARTIAL", bytes(buffer)))

    def _publish(self, entry: TranscriptEntry) -> None:
        self.entries.put(entry)
        if self.on_entry is not None:
            self.on_entry(entry)

    def wait_for_payload(self, expected: str, timeout: float = 3.0) -> TranscriptEntry:
        """Wait for an RX line and fail fast on a board-level ``FAIL`` reply."""
        deadline = monotonic() + max(0.0, timeout)
        while True:
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise TimeoutError(f"Timed out waiting for board reply {expected!r}")
            try:
                entry = self.entries.get(timeout=remaining)
            except queue.Empty as exc:
                raise TimeoutError(f"Timed out waiting for board reply {expected!r}") from exc
            if not entry.direction.startswith("RX"):
                continue
            if entry.payload.upper() in {"FAIL", "ERR", "ERROR"}:
                raise RuntimeError(f"Board rejected the command: {entry.payload}")
            if entry.payload == expected:
                return entry

    def raise_reader_error(self) -> None:
        try:
            error = self.errors.get_nowait()
        except queue.Empty:
            return
        raise RuntimeError("Serial reader stopped unexpectedly") from error

    def stop(self) -> None:
        self._stop_event.set()
        self._reader.join(timeout=1.0)
        self.transcript.close()


def open_multiboard(port: str) -> Any:
    """Open a Multiboard2 serial connection using the documented 115200 8N1."""

    if serial is None:
        raise RuntimeError(
            "pyserial is not installed. Run: python -m pip install -r requirements.txt"
        )
    return serial.Serial(
        port=port,
        baudrate=BAUD_RATE,
        bytesize=serial.EIGHTBITS,
        parity=serial.PARITY_NONE,
        stopbits=serial.STOPBITS_ONE,
        timeout=0.1,
        write_timeout=1.0,
    )


def wait_with_error_check(capture: RawSerialCapture, seconds: float) -> None:
    deadline = monotonic() + seconds
    while monotonic() < deadline:
        capture.raise_reader_error()
        sleep(min(0.05, max(0.0, deadline - monotonic())))
