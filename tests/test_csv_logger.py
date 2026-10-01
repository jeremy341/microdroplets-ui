from __future__ import annotations

import csv
import tempfile
import threading
import time
import unittest
import unittest.mock
from datetime import datetime, timedelta
from pathlib import Path

import backend.csv_logger as backend_csv_logger
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
        raw_value_ml_min=value / 1000.0,
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
            raw_path = path.with_name("flow_raw.csv")
            with raw_path.open(newline="", encoding="utf-8") as file:
                raw_rows = list(csv.reader(file, delimiter=";"))
            self.assertEqual(raw_rows[0][3:6], [
                "Raw line", "Raw value (mL/min)", "Normalized value (µL/min)"
            ])
            self.assertEqual(raw_rows[1][3], "V=2.5")
            self.assertEqual(raw_rows[1][4], "0.0025")
            self.assertEqual(raw_rows[1][5], "2.5")

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

    def test_header_is_written_lazily_for_series_discovered_by_samples(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "flow.csv"
            logger = AsyncCsvLogger(path, interval_seconds=1.0)
            logger.start()
            logger.submit(sample(datetime.now(), value=2.5))
            logger.stop()

            with path.open(newline="", encoding="utf-8") as file:
                content = file.read()
            self.assertIn("Timestamp;COM7 - LiquidFlowRate", content)
            rows = list(csv.reader(content.splitlines(), delimiter=";"))
            self.assertEqual(rows[3], ["Timestamp", "COM7 - LiquidFlowRate"])
            self.assertEqual(rows[4], ["1", "2,500"])

    def test_series_discovered_mid_session_rewrites_the_header(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "flow.csv"
            logger = AsyncCsvLogger(path, interval_seconds=0.1, series_labels=("COM7 - LiquidFlowRate",))
            logger.start()
            start = datetime.now()
            logger.submit(sample(start, "COM7", 1.0))
            logger.submit(sample(start + timedelta(seconds=0.3), "COM7", 2.0))
            # A second board's series appears only after logging started.
            logger.submit(sample(start + timedelta(seconds=0.3), "COM8", 3.0))
            logger.submit(sample(start + timedelta(seconds=0.3), "COM8", 3.5))
            logger.stop()

            with path.open(newline="", encoding="utf-8") as file:
                content = file.read()
            self.assertIn("Timestamp;COM7 - LiquidFlowRate", content)
            self.assertIn("Timestamp;COM7 - LiquidFlowRate;COM8 - LiquidFlowRate", content)

    def test_samples_accepted_before_stop_are_never_dropped(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "flow.csv"
            logger = AsyncCsvLogger(path, interval_seconds=1.0)
            logger.start()
            accepted = 0
            stop_returned = threading.Event()

            def stopper():
                logger.stop(timeout=3.0)
                stop_returned.set()

            start = datetime.now()
            # Pre-load the logger so it is actively writing, then stop while
            # more submissions race in: every sample whose submit() returned
            # True must end up in the raw sidecar.
            for index in range(50):
                if logger.submit(sample(start + timedelta(milliseconds=index), value=1.0 + index)):
                    accepted += 1
            thread = threading.Thread(target=stopper, daemon=True)
            thread.start()
            attempts = 0
            while not stop_returned.is_set() and attempts < 500:
                if logger.submit(sample(start + timedelta(milliseconds=accepted), value=1.0 + accepted)):
                    accepted += 1
                attempts += 1
            thread.join()
            # Let the worker finish draining before the files are asserted
            # and the temporary directory is removed.
            for _ in range(400):
                if logger.status in {"stopped", "failed"}:
                    break
                time.sleep(0.005)

            self.assertIn(logger.status, {"stopped", "failed"})
            self.assertGreater(accepted, 0)
            # Every accepted sample reaches the unsampled raw sidecar even
            # when the interval-aggregated main CSV collapses them.
            with path.with_name("flow_raw.csv").open(newline="", encoding="utf-8") as file:
                raw_rows = [row for row in file.read().splitlines() if row and not row.startswith("Timestamp")]
            self.assertEqual(len(raw_rows), accepted)

    def test_status_reaches_running_after_worker_startup(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "flow.csv"
            logger = AsyncCsvLogger(path, interval_seconds=1.0)
            logger.start()
            for _ in range(200):
                if logger.status == "running":
                    break
                time.sleep(0.005)
            self.assertEqual(logger.status, "running")
            logger.submit(sample(datetime.now(), value=1.0))
            logger.stop()
            self.assertEqual(logger.status, "stopped")

    def test_stop_returns_even_when_worker_died_with_full_queue(self):
        with tempfile.TemporaryDirectory() as directory:
            # The worker fails immediately because the parent path is a file,
            # leaving a full bounded queue with no consumer; stop() must not
            # hang waiting to enqueue the sentinel.
            parent_file = Path(directory) / "not-a-directory"
            parent_file.write_text("x", encoding="utf-8")
            with unittest.mock.patch.object(backend_csv_logger, "QUEUE_LIMIT", 3):
                logger = AsyncCsvLogger(parent_file / "flow.csv", 1.0)
            logger.start()
            for index in range(20):
                logger.submit(sample(datetime.now() + timedelta(milliseconds=index), value=1.0 + index))
            for _ in range(200):
                if logger.status == "failed":
                    break
                time.sleep(0.005)
            self.assertEqual(logger.status, "failed")
            stopper_done = threading.Event()

            def stopper():
                logger.stop(timeout=0.5)
                stopper_done.set()

            thread = threading.Thread(target=stopper, daemon=True)
            thread.start()
            self.assertTrue(stopper_done.wait(2.0))
            thread.join(1.0)
    def test_queue_overflow_drops_oldest_samples_and_counts_them(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "flow.csv"
            with unittest.mock.patch.object(backend_csv_logger, "QUEUE_LIMIT", 5):
                logger = AsyncCsvLogger(path, interval_seconds=1.0)
            logger.start()
            start = datetime.now()
            for index in range(200):
                logger.submit(sample(start + timedelta(milliseconds=index), value=1.0 + index))
            logger.stop()
            self.assertGreater(logger.dropped_samples, 0)
            with path.open(newline="", encoding="utf-8") as file:
                rows = list(csv.reader(file, delimiter=";"))
            # At most the queue limit of samples survived; none was invented.
            self.assertLessEqual(len([r for r in rows if r and r[0].isdigit()]), 5)
            self.assertEqual(logger.status, "stopped")


if __name__ == "__main__":
    unittest.main()
