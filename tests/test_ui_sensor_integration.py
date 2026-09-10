from __future__ import annotations

import os
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtWidgets import QApplication
except ModuleNotFoundError as exc:
    raise unittest.SkipTest("PyQt6 is required for the offscreen UI integration tests") from exc

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
            [
                BackendEvent("measurement", "COM7", sample=sample),
                BackendEvent("measurement", "COM7", sample=sample),
            ]
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
        self.assertEqual(page.histories[key], [10912.0, 10912.0])
        self.assertEqual(len(page.history_timestamps[key]), 2)
        self.assertAlmostEqual(page.accumulated_volumes_ul[key], 125.0)
        self.assertEqual(board["sensor_status"], "Liquid-flow sensor connected")
        self.assertEqual(page.chart.series[0]["id"], key)
        self.assertEqual(page.chart.series[0]["values"], [10912.0, 10912.0])
        self.assertEqual(page.chart.series[0]["timestamps"], [
            timestamp.astimezone().replace(tzinfo=None),
            timestamp.astimezone().replace(tzinfo=None),
        ])

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

    def test_single_board_starts_unselected_with_every_measurement_selectable(self):
        page = SensorsPage()
        self.addCleanup(page.deleteLater)
        board = {
            "name": "MB1", "port": "COM7", "mode": "serial",
            "connection": FakeConnection([]),
            "sensors": [{
                "id": "liquid_flow", "label": "Liquid Flow Rate",
                "unit": "µL/min", "active": False, "available": False,
            }],
        }

        page.set_connected_boards([board])
        card = page.cards_by_id["COM7"]

        self.assertEqual(page.selected_measurement_sets["COM7"], [])
        self.assertNotIn("COM7", page.selected_measurements)
        self.assertTrue(all(not box.isChecked() for box in card.selection_boxes.values()))
        self.assertTrue(all(box.isEnabled() for box in card.selection_boxes.values()))
        self.assertTrue(all(row.isEnabled() for row in card.sensor_rows_by_id.values()))

    def test_single_board_allows_two_and_keeps_selected_rows_enabled(self):
        page = SensorsPage()
        self.addCleanup(page.deleteLater)
        board = {
            "name": "MB1", "port": "COM7", "mode": "serial",
            "connection": FakeConnection([]),
            "sensors": [],
        }
        page.set_connected_boards([board])
        card = page.cards_by_id["COM7"]

        card.selection_boxes["liquid_flow"].setChecked(True)
        card.selection_boxes["pressure"].setChecked(True)

        self.assertEqual(
            page.selected_measurement_sets["COM7"],
            ["liquid_flow", "pressure"],
        )
        self.assertTrue(card.selection_boxes["liquid_flow"].isEnabled())
        self.assertTrue(card.selection_boxes["pressure"].isEnabled())
        self.assertFalse(card.selection_boxes["gas_flow"].isEnabled())
        self.assertTrue(card.sensor_rows_by_id["liquid_flow"].isEnabled())
        self.assertTrue(card.sensor_rows_by_id["pressure"].isEnabled())
        self.assertFalse(card.sensor_rows_by_id["gas_flow"].isEnabled())

        card.selection_boxes["liquid_flow"].setChecked(False)

        self.assertEqual(page.selected_measurement_sets["COM7"], ["pressure"])
        self.assertTrue(card.selection_boxes["gas_flow"].isEnabled())
        self.assertTrue(card.sensor_rows_by_id["gas_flow"].isEnabled())

    def test_sensor_detection_does_not_auto_select_a_measurement(self):
        timestamp = datetime.now(timezone.utc)
        samples = [
            SensorSample(
                timestamp=timestamp + timedelta(milliseconds=index),
                elapsed_seconds=float(index),
                board_port="COM7",
                sensor_id="liquid_flow",
                value=-2000.0,
                unit="µL/min",
                accumulated_volume_ul=0.0,
                raw_line="V=-2",
            )
            for index in range(2)
        ]
        connection = FakeConnection([
            BackendEvent("measurement", "COM7", sample=sample)
            for sample in samples
        ])
        board = {
            "name": "MB1", "port": "COM7", "mode": "serial",
            "connection": connection,
            "sensors": [{
                "id": "liquid_flow", "label": "Liquid Flow Rate",
                "unit": "µL/min", "active": False, "available": False,
            }],
        }
        page = SensorsPage()
        self.addCleanup(page.deleteLater)
        page.set_connected_boards([board])

        page.update_sensor_values()

        self.assertEqual(page.histories["COM7:liquid_flow"], [-2000.0, -2000.0])
        self.assertEqual(page.selected_measurement_sets["COM7"], [])
        self.assertNotIn("COM7", page.selected_measurements)
        self.assertTrue(page.cards_by_id["COM7"].selection_boxes["liquid_flow"].isEnabled())

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

    def test_logging_state_does_not_leave_live_view(self):
        chart = SensorChart()
        self.addCleanup(chart.deleteLater)
        self.assertTrue(chart._follow_live)
        chart.set_recording(True)
        chart.set_recording(False)
        self.assertTrue(chart._follow_live)

    def test_pause_freezes_exact_viewport_while_curve_data_continues(self):
        chart = SensorChart()
        self.addCleanup(chart.deleteLater)
        start = datetime.now().replace(microsecond=0)
        timestamps = [start + timedelta(seconds=index) for index in range(61)]
        chart.set_data(
            [{
                "id": "COM7:liquid_flow",
                "values": [float(index) for index in range(61)],
                "timestamps": timestamps,
                "color": "#0E55FF",
            }],
            [],
        )
        chart._plot.setXRange(timestamps[20].timestamp(), timestamps[40].timestamp(), padding=0)
        chart._view_box.setYRange(10.0, 50.0, padding=0)
        before_x = tuple(chart._view_box.viewRange()[0])
        before_y = tuple(chart._view_box.viewRange()[1])

        chart.pause_view()
        extended_timestamps = timestamps + [start + timedelta(seconds=index) for index in range(61, 81)]
        chart.set_data(
            [{
                "id": "COM7:liquid_flow",
                "values": [float(index) for index in range(81)],
                "timestamps": extended_timestamps,
                "color": "#0E55FF",
            }],
            [],
        )

        after_x = tuple(chart._view_box.viewRange()[0])
        after_y = tuple(chart._view_box.viewRange()[1])
        self.assertEqual(before_x, after_x)
        self.assertEqual(before_y, after_y)
        self.assertFalse(chart._follow_live)
        self.assertTrue(chart._manual_scale)
        self.assertEqual(len(chart.series[0]["values"]), 81)

        chart.resume_live()
        self.assertTrue(chart._follow_live)
        self.assertFalse(chart._manual_scale)
        live_x = chart._view_box.viewRange()[0]
        self.assertAlmostEqual(live_x[1], extended_timestamps[-1].timestamp(), places=2)

    def test_live_history_keeps_ten_minutes_by_time_not_sample_count(self):
        page = SensorsPage()
        self.addCleanup(page.deleteLater)
        key = "COM7:liquid_flow"
        page.measurement_metadata[key] = {
            "label": "Liquid Flow Rate", "precision": 3,
            "unit": "µL/min", "active": True,
        }
        start = datetime.now().replace(microsecond=0)
        page._append_sample("COM7", "liquid_flow", 1.0, start, 0.0)
        page._append_sample("COM7", "liquid_flow", 2.0, start + timedelta(seconds=599), 0.0)
        page._append_sample("COM7", "liquid_flow", 3.0, start + timedelta(seconds=601), 0.0)

        self.assertEqual(page.histories[key], [2.0, 3.0])
        self.assertEqual(len(page.history_timestamps[key]), 2)

    def test_pause_preserves_old_history_until_live_view_resumes(self):
        page = SensorsPage()
        self.addCleanup(page.deleteLater)
        key = "COM7:liquid_flow"
        page.measurement_metadata[key] = {
            "label": "Liquid Flow Rate", "precision": 3,
            "unit": "µL/min", "active": True,
        }
        start = datetime.now().replace(microsecond=0)
        page._append_sample("COM7", "liquid_flow", 1.0, start, 0.0)
        page.chart.pause_view()
        page._append_sample("COM7", "liquid_flow", 2.0, start + timedelta(seconds=601), 0.0)

        self.assertEqual(page.histories[key], [1.0, 2.0])

        page.chart_auto_scale()
        self.assertEqual(page.histories[key], [2.0])
        self.assertTrue(page.chart.is_live_view)

    def test_one_board_can_track_two_measurements_with_two_axes(self):
        board = {
            "name": "MB1",
            "port": "COM7",
            "mode": "serial",
            "connection": FakeConnection([]),
            "sensors": [
                {"id": "liquid_flow", "active": True, "available": True},
                {"id": "pressure", "active": True, "available": True},
                {"id": "gas_flow", "active": True, "available": True},
            ],
        }
        page = SensorsPage()
        self.addCleanup(page.deleteLater)
        page.set_connected_boards([board])
        page.set_active_board(board)
        page.set_measurement("COM7", "liquid_flow", True)
        page.set_measurement("COM7", "pressure", True)
        timestamp = datetime.now()
        page._append_sample("COM7", "liquid_flow", 1200.0, timestamp, 0.0)
        page._append_sample("COM7", "pressure", 20.0, timestamp, None)
        page.refresh_chart()

        self.assertEqual(
            page.selected_measurement_sets["COM7"],
            ["liquid_flow", "pressure"],
        )
        self.assertEqual(
            [series["axis"] for series in page.chart.series],
            ["left", "right"],
        )
        self.assertFalse(
            page.cards_by_id["COM7"].selection_boxes["gas_flow"].isEnabled()
        )

    def test_open_file_explorer_uses_system_folder_window(self):
        page = SensorsPage()
        self.addCleanup(page.deleteLater)
        with tempfile.TemporaryDirectory() as directory:
            page.path_edit.setText(os.path.join(directory, "flow.csv"))
            with patch("ui.pages.Sensors.QDesktopServices.openUrl") as open_url:
                page.choose_log_path()
        open_url.assert_called_once()

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

    def test_ui_does_not_issue_an_uncoordinated_stream_retry(self):
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

        self.assertEqual(connection.retry_count, 0)

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
        connection.events.extend([
            BackendEvent(
                "measurement", "COM7",
                sample=SensorSample(timestamp, 1.0, "COM7", "liquid_flow",
                                    2500.0, "µL/min", 100.0, "V=2.5"),
            ),
            BackendEvent(
                "measurement", "COM7",
                sample=SensorSample(timestamp, 1.5, "COM7", "liquid_flow",
                                    -2.0, "µL/min", 100.0, "V=-0.002"),
            ),
        ])
        page.update_sensor_values()
        self.assertEqual(board["sensor_status"], "Liquid-flow sensor connected")

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

# Pause behavior is intentionally covered in the PyQt integration suite above.
