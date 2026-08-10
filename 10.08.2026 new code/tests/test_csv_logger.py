from __future__ import annotations

import csv
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from backend.csv_logger import AsyncCsvLogger, LogSample


def sample(timestamp, board="COM7", value=1.0):
    return LogSample(
        timestamp=timestamp,
        board_id=board,
        sensor_id="liquid_flow",
        sensor_type="Liquid Flow Rate",
        value=value,
        precision=3,
        unit="µL/min",
        accumulated_volume_ul=250.0,
        raw_line=f"V={value}",
    )


class AsyncCsvLoggerTests(unittest.TestCase):
    def test_writes_real_samples_without_gui_thread_file_io(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "flow.csv"
            logger = AsyncCsvLogger(path, interval_seconds=1.0, series_labels=("COM7 - LiquidFlowRate",))
            logger.start()
            timestamp = datetime.now()
            self.assertTrue(logger.submit(sample(timestamp, value=2.5)))
            logger.stop()

            with path.open(newline="", encoding="utf-8") as file:
                rows = list(csv.reader(file, delimiter=";"))
            self.assertEqual(rows[0], ["Logging Start Time:", rows[0][1]])
            self.assertEqual(rows[1], ["Sample Rate:", "1", "samples/second"])
            self.assertEqual(rows[3], ["Timestamp", "COM7 - LiquidFlowRate"])
            self.assertEqual(rows[4], ["1", "2,500"])

    def test_interval_is_independent_per_board_and_preserves_interval_peaks(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "flow.csv"
            logger = AsyncCsvLogger(path, interval_seconds=1.0, series_labels=("COM7 - LiquidFlowRate", "COM8 - LiquidFlowRate"))
            logger.start()
            start = datetime.now()
            logger.submit(sample(start, "COM7", 1.0))
            logger.submit(sample(start + timedelta(seconds=0.5), "COM7", 2.0))
            logger.submit(sample(start + timedelta(seconds=0.5), "COM8", 3.0))
            logger.submit(sample(start + timedelta(seconds=1.1), "COM7", 4.0))
            logger.stop()

            with path.open(newline="", encoding="utf-8") as file:
                rows = list(csv.reader(file, delimiter=";"))
            self.assertEqual(rows[4], ["1", "2,000", ""])
            self.assertEqual(rows[5], ["2", "", "3,000"])
            self.assertEqual(rows[6], ["3", "4,000", ""])

    def test_short_peak_is_flushed_at_stop_and_keeps_its_signed_direction(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "flow.csv"
            logger = AsyncCsvLogger(path, interval_seconds=1.0, series_labels=("COM7 - LiquidFlowRate",))
            logger.start()
            start = datetime.now()
            logger.submit(sample(start, value=100.0))
            logger.submit(sample(start + timedelta(seconds=0.2), value=60000.0))
            logger.submit(sample(start + timedelta(seconds=0.4), value=250.0))
            logger.stop()

            with path.open(newline="", encoding="utf-8") as file:
                rows = list(csv.reader(file, delimiter=";"))
            self.assertEqual(rows[4], ["1", "60000,000"])

    def test_negative_peak_uses_absolute_magnitude_without_losing_sign(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "flow.csv"
            logger = AsyncCsvLogger(path, interval_seconds=1.0, series_labels=("COM7 - LiquidFlowRate",))
            logger.start()
            start = datetime.now()
            logger.submit(sample(start, value=-100.0))
            logger.submit(sample(start + timedelta(seconds=0.2), value=-60000.0))
            logger.stop()

            with path.open(newline="", encoding="utf-8") as file:
                rows = list(csv.reader(file, delimiter=";"))
            self.assertEqual(rows[4], ["1", "-60000,000"])

    def test_signed_negative_flow_is_written_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "flow.csv"
            logger = AsyncCsvLogger(path, interval_seconds=1.0, series_labels=("COM7 - LiquidFlowRate",))
            logger.start()
            logger.submit(sample(datetime.now(), value=-2.5))
            logger.stop()

            with path.open(newline="", encoding="utf-8") as file:
                rows = list(csv.reader(file, delimiter=";"))
            self.assertEqual(rows[4], ["1", "-2,500"])

    def test_invalid_path_fails_in_worker_without_blocking_caller(self):
        with tempfile.TemporaryDirectory() as directory:
            parent_file = Path(directory) / "not-a-directory"
            parent_file.write_text("x", encoding="utf-8")
            logger = AsyncCsvLogger(parent_file / "flow.csv", 1.0)
            started = time.monotonic()
            logger.start()
            self.assertLess(time.monotonic() - started, 0.2)
            for _ in range(100):
                if logger.status == "failed":
                    break
                time.sleep(0.005)
            self.assertEqual(logger.status, "failed")
            self.assertTrue(logger.error)


if __name__ == "__main__":
    unittest.main()
