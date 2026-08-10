from __future__ import annotations

import queue
import unittest
from unittest.mock import patch

from backend.protocol import Calibration
from backend.serial_manager import MultiboardConnection
from backend.serial_manager import open_and_start_liquid_flow


class FakeSerial:
    def __init__(self) -> None:
        self.is_open = True
        self.writes: list[bytes] = []
        self.reads: queue.Queue[bytes] = queue.Queue()
        self.reset_count = 0

    def read(self, size: int = 1) -> bytes:
        try:
            return self.reads.get(timeout=0.01)
        except queue.Empty:
            return b""

    def write(self, payload: bytes) -> int:
        self.writes.append(payload)
        return len(payload)

    def flush(self) -> None:
        pass

    def reset_input_buffer(self) -> None:
        self.reset_count += 1
        while not self.reads.empty():
            self.reads.get_nowait()

    def close(self) -> None:
        self.is_open = False


class SerialManagerTests(unittest.TestCase):
    def test_open_resets_stale_input_and_emits_connection_state(self) -> None:
        fake = FakeSerial()
        fake.reads.put(b"stale bytes\r\n")
        board = MultiboardConnection("COM7", fake)
        board.open()

        self.assertEqual(fake.reset_count, 1)
        events = board.drain_events()
        states = [event.message for event in events if event.kind == "state"]
        self.assertIn("Serial connection open; waiting for firmware identification", states)
        self.assertNotIn("stale bytes", [event.message for event in events if event.kind == "raw"])
        board.close()

    def test_raw_lines_are_kept_alongside_typed_replies(self) -> None:
        fake = FakeSerial()
        board = MultiboardConnection("COM7", fake)
        board.active_sensor_id = "liquid_flow"
        board._handle_line("unexpected firmware text")

        events = board.drain_events()
        self.assertEqual(
            [event.message for event in events if event.kind == "raw"],
            ["unexpected firmware text"],
        )
        self.assertEqual(
            [event.message for event in events if event.kind == "unknown"],
            ["unexpected firmware text"],
        )

    def test_flow_commands_and_safe_close(self) -> None:
        fake = FakeSerial()
        board = MultiboardConnection("COM7", fake)
        board.open()
        board.start_sensor("liquid_flow", Calibration.WATER)
        board.close()
        self.assertEqual(fake.writes, [b"L0\r\n", b"DFON\r\n", b"DFOFF\r\n"])
        self.assertFalse(fake.is_open)

    def test_fragmented_samples_are_parsed_and_integrated(self) -> None:
        fake = FakeSerial()
        board = MultiboardConnection("COM7", fake)
        board.open()
        board.start_sensor("liquid_flow")
        board.drain_events()

        with patch("backend.serial_manager.monotonic", side_effect=[10.0, 10.5]):
            board._handle_line("V=6")
            board._handle_line("V=6")

        events = board.drain_events()
        samples = [event.sample for event in events if event.sample is not None]
        self.assertEqual(len(samples), 2)
        # The first single sample is intentionally treated as unarmed flow;
        # integration begins after the second confirmed sample.
        self.assertAlmostEqual(samples[-1].accumulated_volume_ul, 25.0, places=6)
        board.close()

    def test_switching_sensor_stops_previous_stream(self) -> None:
        fake = FakeSerial()
        board = MultiboardConnection("COM7", fake)
        board.open()
        board.start_sensor("liquid_flow")
        board.start_sensor("pressure")
        board.close()
        self.assertEqual(
            fake.writes,
            [
                b"L0\r\n", b"DFON\r\n", b"DFOFF\r\n",
                b"DPON\r\n", b"DPOFF\r\n",
            ],
        )

    def test_retry_stream_preserves_accumulated_volume(self) -> None:
        fake = FakeSerial()
        board = MultiboardConnection("COM7", fake)
        board.open()
        board.start_sensor("liquid_flow")
        board._volume_ul = 1250.0

        self.assertTrue(board.retry_active_stream())
        self.assertEqual(fake.writes, [b"L0\r\n", b"DFON\r\n", b"DFON\r\n"])
        self.assertAlmostEqual(board.accumulated_volume_ul, 1250.0)
        board.close()

    def test_long_signal_gap_does_not_invent_accumulated_volume(self) -> None:
        fake = FakeSerial()
        board = MultiboardConnection("COM7", fake)
        board.active_sensor_id = "liquid_flow"
        with patch("backend.serial_manager.monotonic", side_effect=[10.0, 15.0]):
            board._handle_line("V=12.0")
            board._handle_line("V=12.0")
        events = board.drain_events()
        samples = [event.sample for event in events if event.sample is not None]
        self.assertEqual(len(samples), 2)
        self.assertAlmostEqual(samples[-1].accumulated_volume_ul, 0.0)

    def test_motion_spike_is_rejected_and_signed_flow_is_preserved(self) -> None:
        fake = FakeSerial()
        board = MultiboardConnection("COM7", fake)
        board.active_sensor_id = "liquid_flow"
        with patch("backend.serial_manager.monotonic", side_effect=[10.0, 10.5, 11.0]):
            board._handle_line("V=-1000.0")
            board._handle_line("V=-1000.0")
            board._handle_line("V=-1000.0")
        events = board.drain_events()
        samples = [event.sample for event in events if event.sample is not None]
        self.assertEqual(samples[0].value, 0.0)
        self.assertLess(samples[1].value, 0.0)
        self.assertLess(samples[2].value, 0.0)
        self.assertLess(samples[-1].accumulated_volume_ul, 0.0)
        board.close()

    def test_raw_mode_bypasses_filter_but_keeps_normalized_units_and_integration(self) -> None:
        fake = FakeSerial()
        board = MultiboardConnection("COM7", fake, filter_enabled=False)
        board.active_sensor_id = "liquid_flow"
        with patch("backend.serial_manager.monotonic", side_effect=[10.0, 10.5]):
            board._handle_line("V=60")
            board._handle_line("V=60")
        samples = [event.sample for event in board.drain_events() if event.sample is not None]
        self.assertEqual([sample.value for sample in samples], [60000.0, 60000.0])
        self.assertAlmostEqual(samples[-1].accumulated_volume_ul, 500.0, places=6)

    def test_filtered_mode_remains_default(self) -> None:
        board = MultiboardConnection("COM7", FakeSerial())
        self.assertTrue(board.filter_enabled)

    @patch("backend.serial_manager.sleep")
    @patch("backend.serial_manager.MultiboardConnection")
    def test_hardware_handshake_uses_proven_delays(self, board_type, sleep_mock) -> None:
        board = board_type.return_value
        result = open_and_start_liquid_flow("COM7")

        self.assertIs(result, board)
        board.open.assert_called_once_with()
        board.request_firmware.assert_called_once_with()
        board.start_sensor.assert_called_once_with(
            "liquid_flow",
            Calibration.WATER,
            calibration_delay_seconds=0.5,
        )
        self.assertEqual(
            [call.args[0] for call in sleep_mock.call_args_list],
            [1.0, 1.0],
        )


if __name__ == "__main__":
    unittest.main()
