from __future__ import annotations

import os
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from backend.serial_manager import BackendEvent, SensorSample
from ui.pages.Sensors import SensorChart, SensorsPage


class FakeConnection:
    def __init__(self, events):
        self.events = list(events)
        self.retry_count = 0

    def drain_events(self):
        events, self.events = self.events, []
        return events

    def retry_active_stream(self):
        self.retry_count += 1
        return True


class SensorUiIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_backend_sample_reaches_history_card_and_graph(self):
        timestamp = datetime.now(timezone.utc)
        sample = SensorSample(
            timestamp=timestamp,
            elapsed_seconds=1.0,
            board_port="COM7",
            sensor_id="liquid_flow",
            value=10912.0,
            unit="µL/min",
            accumulated_volume_ul=125.0,
            raw_line="V=10.912",
        )
        connection = FakeConnection(
            [BackendEvent("measurement", "COM7", sample=sample)]
        )
        board = {
            "name": "MB1",
            "port": "COM7",
            "mode": "serial",
            "connection": connection,
            "sensor_status": "Starting liquid-flow stream…",
            "sensors": [
                {
                    "id": "liquid_flow",
                    "label": "Liquid Flow Rate",
                    "unit": "µL/min",
                    "precision": 3,
                    "active": True,
                    "available": True,
                }
            ],
        }

        page = SensorsPage()
        self.addCleanup(page.deleteLater)
        page.set_connected_boards([board])
        page.set_active_board(board)
        page.update_sensor_values()

        key = "COM7:liquid_flow"
        self.assertEqual(page.histories[key], [10912.0])
        self.assertEqual(len(page.history_timestamps[key]), 1)
        self.assertAlmostEqual(page.accumulated_volumes_ul[key], 125.0)
        self.assertEqual(board["sensor_status"], "Liquid-flow stream active")
        self.assertEqual(page.chart.series[0]["id"], key)
        self.assertEqual(page.chart.series[0]["values"], [10912.0])
        self.assertEqual(page.chart.series[0]["timestamps"], [timestamp.astimezone().replace(tzinfo=None)])

    def test_liquid_flow_uses_bartels_microlitre_display_unit(self):
        timestamp = datetime.now(timezone.utc)
        sample = SensorSample(
            timestamp=timestamp,
            elapsed_seconds=1.0,
            board_port="COM8",
            sensor_id="liquid_flow",
            value=8374.0,
            unit="µL/min",
            accumulated_volume_ul=200.0,
            raw_line="V=8.374",
        )
        page = SensorsPage()
        self.addCleanup(page.deleteLater)
        page.set_connected_boards([{
            "name": "MB1", "port": "COM8", "mode": "serial",
            "connection": FakeConnection([BackendEvent("measurement", "COM8", sample=sample)]),
            "sensors": [{"id": "liquid_flow", "label": "Liquid Flow Rate", "unit": "µL/min", "active": True, "available": True}],
        }])
        page.update_sensor_values()
        self.assertEqual(page.measurement_metadata["COM8:liquid_flow"]["unit"], "µL/min")
        self.assertEqual(page.histories["COM8:liquid_flow"], [8374.0])
        self.assertIn("µL/min", page.measurement_metadata["COM8:liquid_flow"]["axis_label"])

    def test_live_chart_uses_rolling_30_second_window(self):
        chart = SensorChart()
        self.addCleanup(chart.deleteLater)
        start = datetime.now().replace(microsecond=0)
        timestamps = [start + timedelta(seconds=index) for index in range(101)]
        values = [0.0] * 90 + [7.0, 2.0, 0.0, 63.0, -6.0] + [0.0] * 6
        chart.set_data(
            [{
                "id": "COM7:liquid_flow",
                "values": values,
                "timestamps": timestamps,
                "color": "#0E55FF",
            }],
            [],
            "Liquid flow rate (µL/min)",
        )

        x_low, x_high = chart._view_box.viewRange()[0]
        self.assertAlmostEqual(x_high - x_low, 30.0, places=2)
        self.assertAlmostEqual(x_high, timestamps[-1].timestamp(), places=2)
        self.assertIn(63.0, chart._finite_values(visible_only=True))

    def test_fixed_live_window_and_fit_history_are_distinct(self):
        chart = SensorChart()
        self.addCleanup(chart.deleteLater)
        start = datetime.now().replace(microsecond=0)
        timestamps = [start + timedelta(seconds=index) for index in range(121)]
        chart.set_data(
            [{
                "id": "COM7:liquid_flow",
                "values": [0.0] * len(timestamps),
                "timestamps": timestamps,
                "color": "#0E55FF",
            }],
            [],
        )

        chart.fit_data()
        x_low, x_high = chart._view_box.viewRange()[0]
        self.assertGreater(x_high - x_low, 120.0)

        chart.resume_live()
        x_low, x_high = chart._view_box.viewRange()[0]
        self.assertAlmostEqual(x_high - x_low, 30.0, places=2)

    def test_sensors_page_has_no_live_window_selector(self):
        page = SensorsPage()
        self.addCleanup(page.deleteLater)
        self.assertFalse(hasattr(page, "window_box"))

    def test_positive_flow_scale_keeps_zero_at_bottom(self):
        self.assertEqual(SensorChart._padded_range(0.0, 100.0)[0], 0.0)

    def test_negative_flow_scale_keeps_negative_area_visible(self):
        low, high = SensorChart._padded_range(-100.0, -20.0)
        self.assertLess(low, -100.0)
        self.assertGreater(high, -20.0)

    def test_missing_first_sample_retries_once_without_locking(self):
        connection = FakeConnection([])
        board = {
            "name": "MB1",
            "port": "COM7",
            "mode": "serial",
            "connection": connection,
            "sensor_status": "Starting liquid-flow stream…",
            "sensors": [{
                "id": "liquid_flow", "label": "Liquid Flow Rate",
                "unit": "µL/min", "precision": 3,
                "active": True, "available": True,
            }],
        }
        page = SensorsPage()
        self.addCleanup(page.deleteLater)
        with patch("ui.pages.Sensors.monotonic", return_value=100.0):
            page.set_connected_boards([board])
        with patch("ui.pages.Sensors.monotonic", return_value=112.1):
            page.update_sensor_values()
            page.update_sensor_values()

        self.assertEqual(connection.retry_count, 1)
        self.assertIn("requested again", board["sensor_status"])

    def test_sample_after_pause_recovers_active_status(self):
        timestamp = datetime.now(timezone.utc)
        connection = FakeConnection([])
        board = {
            "name": "MB1", "port": "COM7", "mode": "serial",
            "connection": connection, "sensor_status": "Signal paused",
            "sensors": [{
                "id": "liquid_flow", "label": "Liquid Flow Rate",
                "unit": "µL/min", "precision": 3,
                "active": True, "available": True,
            }],
        }
        page = SensorsPage()
        self.addCleanup(page.deleteLater)
        page.set_connected_boards([board])
        connection.events.append(BackendEvent(
            "measurement", "COM7",
            sample=SensorSample(timestamp, 1.0, "COM7", "liquid_flow",
                                2500.0, "µL/min", 100.0, "V=2.5"),
        ))
        page.update_sensor_values()
        self.assertEqual(board["sensor_status"], "Liquid-flow stream active")

    def test_logging_queues_only_new_samples_and_does_not_use_a_log_timer(self):
        page = SensorsPage()
        self.addCleanup(page.deleteLater)
        self.assertFalse(hasattr(page, "log_timer"))
        self.assertEqual(page.rate_box.currentText(), "1 sec")
        self.assertEqual(page._sample_interval_seconds(), 1.0)

        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "flow.csv")
            page.path_edit.setText(path)
            page.start_logging()
            timestamp = datetime.now()
            metadata = {
                "label": "Liquid Flow Rate", "precision": 3,
                "unit": "µL/min", "active": True,
            }
            page.measurement_metadata["COM7:liquid_flow"] = metadata
            page._append_sample("COM7", "liquid_flow", 2500.0, timestamp, 100.0, "V=2.5")
            page.stop_logging()

            with open(path, encoding="utf-8") as file:
                content = file.read()
            self.assertIn("Logging Start Time:;", content)
            self.assertIn("Sample Rate:;1;samples/second", content)
            self.assertIn("Timestamp;COM7 - LiquidFlowRate", content)
            self.assertIn("1;2500,000", content)

    def test_raw_test_mode_is_recorded_as_a_normal_sensor_sample(self):
        page = SensorsPage()
        self.addCleanup(page.deleteLater)
        metadata = {
            "label": "Liquid Flow Rate", "precision": 3,
            "unit": "µL/min", "active": True,
        }
        page.measurement_metadata["COM7:liquid_flow"] = metadata
        timestamp = datetime.now()
        page._append_sample("COM7", "liquid_flow", 60000.0, timestamp, 500.0, "V=60")
        self.assertEqual(page.histories["COM7:liquid_flow"], [60000.0])
        self.assertEqual(page.session_rows[-1]["raw_line"], "V=60")


if __name__ == "__main__":
    unittest.main()
