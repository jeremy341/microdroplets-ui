from __future__ import annotations

import queue
import threading
import time
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
        # The real Multiboard2 acknowledges every command in wire order;
        # only the firmware identification request answers with version text.
        if payload != b"V\r\n":
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


class QuietSerial(FakeSerial):
    """Never replies on its own; tests queue the exact reply lines."""

    def write(self, payload: bytes) -> int:
        self.writes.append(payload)
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

    def test_late_ok_is_attributed_to_the_timed_out_command(self) -> None:
        # A reply that arrives after its command timed out must be consumed by
        # that command's FIFO entry, never by the next pending transaction.
        fake = QuietSerial()
        board = MultiboardConnection("COM7", fake)
        board.open()
        board.active_sensor_id = "liquid_flow"

        # DFOFF times out without a reply; its entry stays queued.
        self.assertFalse(board.send_and_wait_for_ack("DFOFF", timeout=0.05))
        # L0 also times out; two commands now await replies.
        self.assertFalse(board.send_and_wait_for_ack("L0", timeout=0.05))
        # One late OK arrives: it must pop DFOFF (the head), leaving L0 queued.
        fake.reads.put(b"OK\r\n")
        time.sleep(0.05)
        self.assertEqual(len(board._ack_queue), 1)
        self.assertEqual(board._ack_queue[0].command, "L0")
        board.close()

    def test_ack_reply_is_consumed_in_command_order(self) -> None:
        fake = FakeSerial()
        board = MultiboardConnection("COM7", fake)
        board.open()
        board.active_sensor_id = "liquid_flow"
        results: queue.Queue[bool] = queue.Queue()

        def first_wait() -> None:
            results.put(board.send_and_wait_for_ack("DFOFF", timeout=1.0))

        thread = threading.Thread(target=first_wait, daemon=True)
        thread.start()
        # Give the first transaction time to register before the reply.
        time.sleep(0.05)
        fake.reads.put(b"OK\r\n")
        self.assertTrue(results.get(timeout=1.0))
        thread.join(timeout=1.0)
        board.close()

    def test_error_reply_is_attributed_in_order_and_does_not_block_the_next_command(self) -> None:
        # An ERR line belongs to the oldest outstanding command. A pending
        # L0 transaction behind a timed-out DFOFF must survive the error and
        # still be acknowledged by its own reply.
        fake = QuietSerial()
        board = MultiboardConnection("COM7", fake)
        board.open()
        board.active_sensor_id = "liquid_flow"
        results: queue.Queue[bool] = queue.Queue()

        self.assertFalse(board.send_and_wait_for_ack("DFOFF", timeout=0.05))

        def wait_for_ack() -> None:
            results.put(board.send_and_wait_for_ack("L0", timeout=1.0))

        thread = threading.Thread(target=wait_for_ack, daemon=True)
        thread.start()
        time.sleep(0.05)
        board._handle_line("ERR: unrelated sensor fault")
        fake.reads.put(b"OK\r\n")
        self.assertTrue(results.get(timeout=1.0))
        thread.join(timeout=1.0)
        board.close()

    def test_slow_stream_intervals_are_still_integrated(self) -> None:
        fake = FakeSerial()
        board = MultiboardConnection("COM7", fake)
        board.active_sensor_id = "liquid_flow"
        with patch("backend.serial_manager.monotonic", side_effect=[10.0, 12.5, 15.0]):
            board._handle_line("V=6")
            board._handle_line("V=6")
            board._handle_line("V=6")
        events = board.drain_events()
        samples = [event.sample for event in events if event.sample is not None]
        self.assertEqual(len(samples), 3)
        # First 2.5 s interval exceeds the 2 s baseline and is skipped; after
        # the cadence estimate adapts, subsequent 2.5 s intervals integrate:
        # 6000 µL/min × 2.5 s / 60 = 250 µL.
        self.assertAlmostEqual(samples[-1].accumulated_volume_ul, 250.0, places=6)
        board.close()

    def test_watchdog_retry_preserves_volume(self) -> None:
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
        board._volume_ul = 6770.0
        # A retry (as the watchdog does) must not wipe the running session's
        # accumulated volume.
        board._reset_integration(keep_total=True)
        self.assertAlmostEqual(board.accumulated_volume_ul, 6770.0)
        board.close()

    def test_volume_reset_racing_with_integration_does_not_crash(self) -> None:
        fake = FakeSerial()
        board = MultiboardConnection("COM7", fake)
        board.open()
        board.active_sensor_id = "liquid_flow"
        stop = threading.Event()
        errors: list[BaseException] = []

        def reset_loop() -> None:
            while not stop.is_set():
                board.reset_accumulated_volume()
                time.sleep(0.001)

        reset_thread = threading.Thread(target=reset_loop, daemon=True)
        reset_thread.start()
        try:
            with patch("backend.serial_manager.monotonic", side_effect=lambda: time.monotonic()):
                for _ in range(2000):
                    try:
                        board._handle_line("V=1.25")
                    except BaseException as exc:  # reader-death regression
                        errors.append(exc)
                        break
        finally:
            stop.set()
            reset_thread.join(timeout=1.0)
        self.assertEqual(errors, [])
        board.close()

    def test_close_finishes_teardown_even_when_poff_is_not_acknowledged(self) -> None:
        class SilentSerial(FakeSerial):
            def write(self, payload: bytes) -> int:
                self.writes.append(payload)
                return len(payload)

        fake = SilentSerial()
        board = MultiboardConnection("COM7", fake)
        board.open()
        board.active_sensor_id = "liquid_flow"
        self.assertFalse(board.close())
        self.assertIn(b"DFOFF\r\n", fake.writes)
        self.assertFalse(fake.is_open)
        self.assertEqual(board.state, "disconnected")


if __name__ == "__main__":
    unittest.main()
