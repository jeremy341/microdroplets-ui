from __future__ import annotations

import csv
import queue
import tempfile
import unittest
from pathlib import Path

from backend.diagnostics import RawSerialCapture, TranscriptWriter, encode_command


class FakeSerial:
    def __init__(self, chunks: list[bytes] | None = None) -> None:
        self.is_open = True
        self.writes: list[bytes] = []
        self._chunks: queue.Queue[bytes] = queue.Queue()
        for chunk in chunks or []:
            self._chunks.put(chunk)

    def read(self, size: int = 1) -> bytes:
        try:
            return self._chunks.get(timeout=0.02)
        except queue.Empty:
            return b""

    def write(self, data: bytes) -> int:
        self.writes.append(data)
        return len(data)

    def flush(self) -> None:
        pass

    def close(self) -> None:
        self.is_open = False


class DiagnosticsTests(unittest.TestCase):
    def test_command_has_exact_crlf_terminator(self) -> None:
        self.assertEqual(encode_command(" DFON "), b"DFON\r\n")
        with self.assertRaises(ValueError):
            encode_command("DFON\nDFOFF")

    def test_capture_records_fragmented_lines_and_commands(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "capture.csv"
            writer = TranscriptWriter(path, started_at=0.0)
            fake = FakeSerial([b"RSL", b"F=1.23\r\nRSLF=", b"0.00\r\n"])
            capture = RawSerialCapture(fake, writer)
            capture.start()
            capture.send("V")

            import time

            time.sleep(0.1)
            capture.stop()

            self.assertEqual(fake.writes, [b"V\r\n"])
            with path.open(newline="", encoding="utf-8") as file:
                rows = list(csv.DictReader(file))
            self.assertEqual(len(rows), 3)
            self.assertEqual(sum(row["direction"] == "TX" for row in rows), 1)
            received = [row["payload"] for row in rows if row["direction"] == "RX"]
            self.assertEqual(received, ["RSLF=1.23", "RSLF=0.00"])

    def test_wait_for_payload_returns_ack_and_rejects_fail(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "capture.csv"
            writer = TranscriptWriter(path, started_at=0.0)
            fake = FakeSerial()
            capture = RawSerialCapture(fake, writer)
            capture.start()
            capture._publish(writer.append("RX", b"OK\r\n"))
            self.assertEqual(capture.wait_for_payload("OK").payload, "OK")
            capture._publish(writer.append("RX", b"FAIL\r\n"))
            with self.assertRaises(RuntimeError):
                capture.wait_for_payload("OK")
            capture.stop()


if __name__ == "__main__":
    unittest.main()
