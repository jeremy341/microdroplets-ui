"""Safe DFON/DFOFF sensor-stream capture and offline analysis.

The probe deliberately sends no pump command.  It records raw bytes first and
only then applies the existing protocol parser so an unknown firmware format
is never silently lost.
"""

from __future__ import annotations

from dataclasses import dataclass
from time import monotonic, sleep
from typing import Any

from backend.protocol import encode_command

# Keep the recorder's record type in one place without importing serial.
from backend.raw_serial_capture import CaptureRecord, _timestamp


@dataclass(frozen=True)
class StreamAnalysis:
    start_ack: bool
    measurement_lines: tuple[str, ...]
    measurement_format: str
    stop_ack: bool
    post_stop_rx_records: int
    post_stop_bytes: int
    stopped_cleanly: bool


def _read_for(connection: Any, seconds: float, started: float, records: list[CaptureRecord]) -> int:
    deadline = monotonic() + max(0.0, seconds)
    count = 0
    while monotonic() < deadline:
        waiting = int(getattr(connection, "in_waiting", 0) or 0)
        chunk = connection.read(waiting or 1)
        if not chunk:
            continue
        data = bytes(chunk)
        records.append(CaptureRecord("RX", _timestamp(), monotonic() - started,
                                     data.hex(" "), data.decode("utf-8", errors="replace")))
        count += len(data)
    return count


def capture_sensor_stream(connection: Any, stream_seconds: float = 10.0,
                          settle_seconds: float = 0.6, post_stop_seconds: float = 2.0
                          ) -> list[CaptureRecord]:
    """Run DFOFF -> L0 -> DFON -> capture -> DFOFF, recording raw TX/RX."""
    records: list[CaptureRecord] = []
    started = monotonic()

    def send(command: str) -> None:
        payload = encode_command(command)
        connection.write(payload)
        connection.flush()
        records.append(CaptureRecord("TX", _timestamp(), monotonic() - started,
                                     payload.hex(" "), command))
        sleep(max(0.0, settle_seconds))
        _read_for(connection, settle_seconds, started, records)

    send("DFOFF")
    send("L0")
    send("DFON")
    _read_for(connection, stream_seconds, started, records)
    send("DFOFF")
    _read_for(connection, post_stop_seconds, started, records)
    return records


def analyze_sensor_stream(records: list[CaptureRecord]) -> StreamAnalysis:
    """Summarize a raw capture without assuming one particular line format."""
    tx = [r.payload_text.strip().upper() for r in records if r.direction == "TX"]
    rx_text = "".join(r.payload_text for r in records if r.direction == "RX")
    lines = tuple(line.strip() for line in rx_text.replace("\r", "\n").split("\n") if line.strip())
    measurement_lines = tuple(line for line in lines if line.upper().startswith(("V=", "RSLF", "FLOW")))
    # Associate OK replies with the nearest preceding command in the raw log.
    last_command = None
    start_ack = False
    stop_ack = False
    post_stop_rx_records = 0
    post_stop_bytes = 0
    stopped = False
    dfoff_seen = 0
    for record in records:
        if record.direction == "TX":
            last_command = record.payload_text.strip().upper()
            if last_command == "DFOFF":
                dfoff_seen += 1
                # The first DFOFF is preparation; the second is the actual stop.
                if dfoff_seen == 2:
                    stopped = True
        elif record.direction == "RX":
            if last_command == "DFON" and "OK" in record.payload_text.upper():
                start_ack = True
            if stopped:
                post_stop_rx_records += 1
                meaningful = [line for line in record.payload_text.replace("\r", "\n").split("\n")
                              if line.strip() and line.strip().upper() != "OK"]
                post_stop_bytes += len("\n".join(meaningful).encode("utf-8"))
            if last_command == "DFOFF" and "OK" in record.payload_text.upper():
                stop_ack = True

    formats = {"V=number" if line.upper().startswith("V=") else
               "RSLF/marker" if line.upper().startswith("RSLF") else
               "FLOW/marker" if line.upper().startswith("FLOW") else "other"
               for line in measurement_lines}
    measurement_format = ", ".join(sorted(formats)) if formats else "no recognized measurement lines"
    return StreamAnalysis(start_ack, measurement_lines, measurement_format, stop_ack,
                          post_stop_rx_records, post_stop_bytes,
                          stop_ack and post_stop_bytes == 0)
