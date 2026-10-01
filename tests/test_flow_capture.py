import csv
import tempfile
import unittest
from pathlib import Path

from backend.diagnostics import encode_command
from backend.flow_capture import (
    CsvCapture,
    documents_dir,
    is_flow_input,
    measurement_schedule,
    parse_liquid_flow,
    unique_csv_path,
)


class FlowCaptureHelpers(unittest.TestCase):
    def test_parser_preserves_board_value_in_ml_per_minute(self):
        self.assertEqual(parse_liquid_flow("V=1.25"), 1.25)
        self.assertEqual(parse_liquid_flow("RSLF=-0.5"), -0.5)
        self.assertIsNone(parse_liquid_flow("OK"))

    def test_unique_path_never_overwrites(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            first = unique_csv_path(directory)
            first.touch()
            second = unique_csv_path(directory)
            self.assertNotEqual(first, second)

    def test_flow_trigger_uses_absolute_raw_value(self):
        self.assertTrue(is_flow_input(0.1, 0.1))
        self.assertTrue(is_flow_input(-0.25, 0.1))
        self.assertFalse(is_flow_input(0.099, 0.1))

    def test_documents_directory_is_directory(self):
        self.assertTrue(documents_dir().is_dir())

    def test_measurement_schedule_has_automatic_trigger_and_post_phases(self):
        self.assertEqual(
            measurement_schedule(5.0, 8.0, 5.0),
            ("baseline", "armed", "flow", "post"),
        )

    def test_measurement_schedule_rejects_invalid_durations(self):
        with self.assertRaises(ValueError):
            measurement_schedule(-1.0, 8.0, 5.0)
        with self.assertRaises(ValueError):
            measurement_schedule(5.0, 0.0, 5.0)

    def test_command_encoding_matches_diagnostics_transport(self):
        self.assertEqual(encode_command("V"), b"V\r\n")

    def test_csv_capture_writes_phase_and_flow_columns(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "capture.csv"
            capture = CsvCapture(path)
            try:
                capture.write("TX", b"V\r\n", "baseline")
                capture.write("RX", b"V=1.25\n", "flow", flow_ml_min=1.25,
                              interval_seconds=0.1, integrated_volume_ul=2.0)
            finally:
                capture.close()
            rows = list(csv.reader(path.open(newline="", encoding="utf-8")))
            self.assertEqual(rows[0], list(CsvCapture.HEADER))
            self.assertEqual(rows[1][3], "TX")
            self.assertEqual(rows[2][6], "1.250000000")
            self.assertEqual(rows[2][7], "1250.000000")

    def test_csv_capture_refuses_to_overwrite(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "capture.csv"
            capture = CsvCapture(path)
            capture.close()
            with self.assertRaises(FileExistsError):
                CsvCapture(path)


if __name__ == "__main__":
    unittest.main()
