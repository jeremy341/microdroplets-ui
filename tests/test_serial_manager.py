from __future__ import annotations

import queue
import unittest
from unittest.mock import call, patch

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
        if payload == b"POFF\r\n":
            self.reads.put(b"<< OK\r\n")
        return len(payload)

    def flush(self) -> None:
        pass

    def reset_input_buffer(self) -> None:
        self.reset_count += 1
        while not self.reads.empty():
            self.reads.get_nowait()

    def close(self) -> None:
        self.is_open = False


class SensorHandshakeSerial(FakeSerial):
    def write(self, payload: bytes) -> int:
        self.writes.append(payload)
        if payload == b"V\r\n":
            self.reads.put(b"Multiboard Ready\r\n")
        elif payload in {b"DFOFF\r\n", b"L0\r\n", b"DFON\r\n", b"POFF\r\n"}:
            self.reads.put(b"OK\r\n")
            if payload == b"DFON\r\n":
                # Zero and negative flow are both valid sensor evidence.
                self.reads.put(b"V=0.000\r\nV=-0.002\r\n")
        return len(payload)


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
        self.assertEqual(
            fake.writes,
            [b"L0\r\n", b"DFON\r\n", b"POFF\r\n", b"DFOFF\r\n"],
        )
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
        # Integration uses the two received raw values directly:
        # 6000 µL/min × 0.5 s / 60 = 50 µL.
        self.assertAlmostEqual(samples[-1].accumulated_volume_ul, 50.0, places=6)
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
                b"DPON\r\n", b"POFF\r\n", b"DPOFF\r\n",
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

    def test_signed_flow_is_preserved_without_filtering(self) -> None:
        fake = FakeSerial()
        board = MultiboardConnection("COM7", fake)
        board.active_sensor_id = "liquid_flow"
        with patch("backend.serial_manager.monotonic", side_effect=[10.0, 10.5, 11.0]):
            board._handle_line("V=-1000.0")
            board._handle_line("V=-1000.0")
            board._handle_line("V=-1000.0")
        events = board.drain_events()
        samples = [event.sample for event in events if event.sample is not None]
        self.assertEqual(samples[0].value, -1000000.0)
        self.assertLess(samples[1].value, 0.0)
        self.assertLess(samples[2].value, 0.0)
        self.assertLess(samples[-1].accumulated_volume_ul, 0.0)
        board.close()

    def test_acknowledged_detection_accepts_zero_and_negative_samples(self) -> None:
        fake = SensorHandshakeSerial()
        board = MultiboardConnection("COM7", fake)
        board.open()
        self.assertTrue(
            board.initialize_liquid_flow(
                max_attempts=1,
                monitor_stream=False,
                sample_timeout_seconds=1.0,
            )
        )
        samples = [
            event.sample for event in board.drain_events() if event.sample is not None
        ]
        self.assertEqual([sample.value for sample in samples], [0.0, -2.0])
        self.assertEqual(
            fake.writes[:4],
            [b"V\r\n", b"DFOFF\r\n", b"L0\r\n", b"DFON\r\n"],
        )
        board.close()

    def test_default_mode_keeps_normalized_units_and_integrates_raw_values(self) -> None:
        fake = FakeSerial()
        board = MultiboardConnection("COM7", fake)
        board.active_sensor_id = "liquid_flow"
        with patch("backend.serial_manager.monotonic", side_effect=[10.0, 10.5]):
            board._handle_line("V=60")
            board._handle_line("V=60")
        samples = [event.sample for event in board.drain_events() if event.sample is not None]
        self.assertEqual([sample.value for sample in samples], [60000.0, 60000.0])
        self.assertAlmostEqual(samples[-1].accumulated_volume_ul, 500.0, places=6)

    @patch("backend.serial_manager.sleep")
    @patch("backend.serial_manager.MultiboardConnection")
    def test_compatibility_helper_uses_acknowledged_initialization(self, board_type, sleep_mock) -> None:
        board = board_type.return_value
        result = open_and_start_liquid_flow("COM7")

        self.assertIs(result, board)
        board.open.assert_called_once_with()
        board.initialize_liquid_flow.assert_called_once_with(
            max_attempts=3,
            monitor_stream=False,
        )
        self.assertEqual(
            [call.args[0] for call in sleep_mock.call_args_list],
            [1.0],
        )


if __name__ == "__main__":
    unittest.main()
