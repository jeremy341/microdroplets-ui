from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.raw_serial_capture import CaptureRecord
from backend.sensor_stream_capture import analyze_sensor_stream


def test_stream_analysis_recognizes_v_format_and_clean_stop():
    records = [
        CaptureRecord("TX", "t", 0.0, "", "DFOFF"),
        CaptureRecord("RX", "t", 0.1, "", "OK\r\n"),
        CaptureRecord("TX", "t", 0.0, "", "DFON"),
        CaptureRecord("RX", "t", 0.1, "", "OK\r\nV=0.012\r\n"),
        CaptureRecord("TX", "t", 1.0, "", "DFOFF"),
        CaptureRecord("RX", "t", 1.1, "", "OK\r\n"),
    ]
    result = analyze_sensor_stream(records)
    assert result.start_ack
    assert result.measurement_lines == ("V=0.012",)
    assert result.measurement_format == "V=number"
    assert result.stop_ack
    assert result.stopped_cleanly


def test_stream_analysis_keeps_unknown_measurement_format_visible():
    records = [
        CaptureRecord("TX", "t", 0.0, "", "DFOFF"),
        CaptureRecord("RX", "t", 0.1, "", "OK\r\n"),
        CaptureRecord("TX", "t", 0.0, "", "DFON"),
        CaptureRecord("RX", "t", 0.1, "", "OK\r\n12.3\r\n"),
        CaptureRecord("TX", "t", 1.0, "", "DFOFF"),
        CaptureRecord("RX", "t", 1.1, "", "OK\r\n"),
    ]
    result = analyze_sensor_stream(records)
    assert not result.measurement_lines
    assert result.measurement_format == "no recognized measurement lines"
