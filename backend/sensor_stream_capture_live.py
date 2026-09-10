"""Live-display wrapper for the safe Multiboard2 sensor-stream probe."""

from __future__ import annotations

from time import monotonic, sleep
from typing import Any, Callable

from backend.protocol import encode_command
from backend.raw_serial_capture import CaptureRecord, _timestamp


RecordCallback = Callable[[CaptureRecord], None]


def _read_for(
    connection: Any,
    seconds: float,
    started: float,
    records: list[CaptureRecord],
    on_record: RecordCallback | None,
) -> None:
    deadline = monotonic() + max(0.0, seconds)
    while monotonic() < deadline:
        waiting = int(getattr(connection, "in_waiting", 0) or 0)
        chunk = connection.read(waiting or 1)
        if not chunk:
            continue
        data = bytes(chunk)
        record = CaptureRecord(
            "RX", _timestamp(), monotonic() - started,
            data.hex(" "), data.decode("utf-8", errors="replace")
        )
        records.append(record)
        if on_record is not None:
            on_record(record)


def _send_and_read(
    connection: Any,
    command: str,
    settle_seconds: float,
    started: float,
    records: list[CaptureRecord],
    on_record: RecordCallback | None,
) -> None:
    """Send one command and finish reading its reply before the next command.

    In particular, L0 must be separated from DFON.  Otherwise a delayed or
    split firmware reply can make the next command look like ``\ufffdL0`` in a
    raw terminal capture even though Python sent the correct ASCII bytes.
    """
    payload = encode_command(command)
    connection.write(payload)
    connection.flush()
    record = CaptureRecord(
        "TX", _timestamp(), monotonic() - started,
        payload.hex(" "), command
    )
    records.append(record)
    if on_record is not None:
        on_record(record)

    # Give the board time to answer, then drain the complete command reply
    # before sending anything else. The raw bytes are still preserved.
    sleep(max(0.0, settle_seconds))
    _read_for(connection, settle_seconds, started, records, on_record)


def _synchronize_board(
    connection: Any,
    settle_seconds: float,
    started: float,
    records: list[CaptureRecord],
    on_record: RecordCallback | None,
) -> None:
    """Synchronize with the boot/firmware banner before control commands.

    A freshly opened Multiboard can deliver the delayed ``Multiboard Ready``
    banner after the first command has already been written. That makes the
    next command's reply look corrupted (for example ``Wrong command`` with a
    stray byte before ``L0``). Asking for ``V`` first and draining its reply
    removes that race without touching pump state.
    """
    _send_and_read(connection, "V", settle_seconds, started, records, on_record)
    reset_input = getattr(connection, "reset_input_buffer", None)
    if callable(reset_input):
        reset_input()


def capture_sensor_stream_live(
    connection: Any,
    stream_seconds: float = 10.0,
    settle_seconds: float = 0.6,
    post_stop_seconds: float = 2.0,
    on_record: RecordCallback | None = None,
) -> list[CaptureRecord]:
    """Run the safe stream sequence and notify the caller for every record."""
    records: list[CaptureRecord] = []
    started = monotonic()

    _synchronize_board(connection, settle_seconds, started, records, on_record)
    _send_and_read(connection, "DFOFF", settle_seconds, started, records, on_record)
    _send_and_read(connection, "L0", settle_seconds, started, records, on_record)
    _send_and_read(connection, "DFON", settle_seconds, started, records, on_record)
    _read_for(connection, stream_seconds, started, records, on_record)
    _send_and_read(connection, "DFOFF", settle_seconds, started, records, on_record)
    _read_for(connection, post_stop_seconds, started, records, on_record)
    return records
