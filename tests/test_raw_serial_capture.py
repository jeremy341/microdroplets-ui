from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.raw_serial_capture import write_capture, CaptureRecord


def test_write_capture_preserves_bytes_and_escapes_text(tmp_path):
    output = tmp_path / "capture.tsv"
    write_capture(
        output,
        [CaptureRecord("RX", "2026-08-11T00:00:00Z", 0.1, "56 0d 0a", "V\r\n")],
    )
    text = output.read_text(encoding="utf-8")
    assert "payload_hex" in text
    assert "56 0d 0a" in text
    assert "V\\r\\n" in text
