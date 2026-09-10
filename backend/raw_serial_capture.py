"""Small, dependency-light raw USB-Serial recorder for Multiboard2.

This module deliberately does not parse or reinterpret board responses. The
raw transcript is the source of truth for later protocol work. See
``docs/DEVELOPER_GUIDE.md`` for the safe probe workflows.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic, sleep
from typing import Any, Iterable

from backend.protocol import encode_command


@dataclass(frozen=True)
class CaptureRecord:
    direction: str
    timestamp_utc: str
    elapsed_seconds: float
    payload_hex: str
    payload_text: str


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def capture_commands(
    connection: Any,
    commands: Iterable[str],
    settle_seconds: float = 0.6,
    read_window_seconds: float = 1.0,
) -> list[CaptureRecord]:
    """Send commands and record every raw byte received during each window.

    The connection must already be opened with 115200 8N1.  No command is
    inferred, retried, or silently changed here.
    """

    records: list[CaptureRecord] = []
    started = monotonic()
    for command in commands:
        # An empty command is the manual's "Enter" probe and is intentionally
        # represented as a bare CRLF. Normal commands still use the strict
        # encoder so malformed input cannot reach the serial port.
        payload = b"\r\n" if command == "" else encode_command(command)
        connection.write(payload)
        connection.flush()
        records.append(
            CaptureRecord("TX", _timestamp(), monotonic() - started, payload.hex(" "), command)
        )
        sleep(max(0.0, settle_seconds))
        deadline = monotonic() + max(0.0, read_window_seconds)
        while monotonic() < deadline:
            waiting = int(getattr(connection, "in_waiting", 0) or 0)
            chunk = connection.read(waiting or 1)
            if not chunk:
                continue
            records.append(
                CaptureRecord(
                    "RX",
                    _timestamp(),
                    monotonic() - started,
                    bytes(chunk).hex(" "),
                    bytes(chunk).decode("utf-8", errors="replace"),
                )
            )
    return records


def write_capture(path: Path, records: Iterable[CaptureRecord]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write("direction\ttimestamp_utc\telapsed_seconds\tpayload_hex\tpayload_text\n")
        for record in records:
            text = record.payload_text.replace("\t", "\\t").replace("\r", "\\r").replace("\n", "\\n")
            handle.write(
                f"{record.direction}\t{record.timestamp_utc}\t{record.elapsed_seconds:.6f}"
                f"\t{record.payload_hex}\t{text}\n"
            )
