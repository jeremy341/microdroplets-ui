from __future__ import annotations

import queue
import threading
import time
import unittest
from time import monotonic
from unittest.mock import patch

import serial

from backend.channel_ownership import ChannelOwnershipManager
from backend.protocol import Calibration
from backend.pump_control import PumpControlService
from backend.serial_manager import (
    EVENT_QUEUE_LIMIT,
    MAX_PENDING_LINE_BYTES,
    STREAM_REQUESTED,
    STREAMING,
    BackendEvent,
    MultiboardConnection,
    open_and_start_liquid_flow,
)


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


class SlowAckSerial(FakeSerial):
    """A healthy board whose reply to one command arrives late.

    Used to reproduce a disconnect that lands in the middle of a pump start
    transaction: the amplitude command is still unacknowledged when close()
    sends POFF, so the ON command is written afterwards.
    """

    def __init__(self, slow_command: bytes = b"P1V120\r\n", delay: float = 0.5) -> None:
        super().__init__()
        self.slow_command = slow_command
        self.delay = delay
        self.timeline: list[tuple[float, bytes]] = []

    def write(self, payload: bytes) -> int:
        self.writes.append(payload)
        self.timeline.append((monotonic(), payload))
        if payload != b"V\r\n":
            if payload == self.slow_command:
                time.sleep(self.delay)
            self.reads.put(b"OK\r\n")
        return len(payload)


class PressureOnlyHandshakeSerial(FakeSerial):
    """Board that passes the liquid-flow handshake but only streams pressure."""

    def write(self, payload: bytes) -> int:
        self.writes.append(payload)
        if payload == b"V\r\n":
            self.reads.put(b"Multiboard Ready\r\n")
        elif payload in {b"DFOFF\r\n", b"L0\r\n", b"DFON\r\n", b"POFF\r\n"}:
            self.reads.put(b"OK\r\n")
            if payload == b"DFON\r\n":
                self.reads.put(b"RSDPC=120.5\r\nRSDPC=121.0\r\n")
        return len(payload)


class FailingWriteSerial(FakeSerial):
    """Board whose transport rejects specific commands before any byte is sent.

    Reproduces a write timeout / SerialException on real hardware: the command
    never reaches the board, so nothing will ever acknowledge it.
    """

    def __init__(self, failing_payloads: tuple[bytes, ...] = (b"DFOFF\r\n",)) -> None:
        super().__init__()
        self.failing_payloads = failing_payloads

    def write(self, payload: bytes) -> int:
        if payload in self.failing_payloads:
            raise serial.SerialException("write timed out")
        return super().write(payload)


# Non-delimiter bytes, so a block of this never splits a protocol line.
_NOISE_UNIT = b"\xff\xfe\xa5\x00"


class ScriptedReadSerial:
    """Replays a fixed chunk script, then asks the reader loop to stop.

    Running ``_reader_loop()`` in the calling thread (instead of the reader
    thread) makes the receive-buffer bound deterministic: every chunk is
    consumed in a known order and the loop exits as soon as the script is spent.
    """

    def __init__(self, script: list[bytes], stop_event: threading.Event) -> None:
        self._script = list(script)
        self._stop_event = stop_event
        self.is_open = True
        self.writes: list[bytes] = []

    def read(self, size: int = 1) -> bytes:
        if self._script:
            return self._script.pop(0)
        self._stop_event.set()
        return b""

    def write(self, payload: bytes) -> int:
        self.writes.append(payload)
        return len(payload)

    def flush(self) -> None:
        pass

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

    def test_pump_start_transaction_cannot_write_on_after_poff(self) -> None:
        # A disconnect that lands mid-transaction must not let the remaining
        # start-up commands reach the wire after the global power-off.
        # Otherwise the board keeps driving a channel the app believes is off.
        fake = SlowAckSerial(slow_command=b"P1V120\r\n", delay=0.5)
        board = MultiboardConnection("COM7", fake)
        board.open()
        pump = PumpControlService(board, ChannelOwnershipManager())

        outcome: list[object] = []

        def start() -> None:
            outcome.append(pump.start_manual(0, 100, "Sinus", 1, 120))

        worker = threading.Thread(target=start, daemon=True)
        worker.start()
        time.sleep(0.15)  # inside the start transaction, before P1ON
        board.close()
        worker.join(3.0)

        on_positions = [
            stamp for stamp, payload in fake.timeline if payload == b"P1ON\r\n"
        ]
        poff_positions = [
            stamp for stamp, payload in fake.timeline if payload == b"POFF\r\n"
        ]
        # Either P1ON was never written (gated), or it precedes POFF. It must
        # never be written after the global power-off.
        self.assertFalse(
            on_positions and poff_positions and on_positions[0] > poff_positions[0],
            f"ON after POFF: on={on_positions} poff={poff_positions}",
        )
        # And the torn-down transaction must not report success.
        if outcome:
            self.assertFalse(getattr(outcome[0], "success", False))

    @staticmethod
    def _await_registration(board: MultiboardConnection, expected: int = 1) -> None:
        """Block until a worker thread's command is actually queued."""

        deadline = monotonic() + 2.0
        while len(board._ack_queue) < expected and monotonic() < deadline:
            time.sleep(0.005)

    def test_failed_write_does_not_leave_a_phantom_ack_entry(self) -> None:
        # A command whose bytes never reach the board is never acknowledged, so
        # its FIFO entry must not survive the exception: the next command's OK
        # would be consumed by the dead entry and reported as unacknowledged.
        fake = FailingWriteSerial(failing_payloads=(b"DFOFF\r\n", b"L0\r\n"))
        board = MultiboardConnection("COM7", fake)
        board.open()
        board.active_sensor_id = "liquid_flow"

        with self.assertRaises(serial.SerialException):
            board.send_and_wait_for_ack("DFOFF", timeout=1.0)
        self.assertEqual(list(board._ack_queue), [])
        with self.assertRaises(serial.SerialException):
            board.send("L0")
        self.assertEqual(list(board._ack_queue), [])

        # The board is healthy: this OK belongs to DFON and must acknowledge it.
        self.assertTrue(board.send_and_wait_for_ack("DFON", timeout=1.0))
        self.assertEqual(list(board._ack_queue), [])
        board.close()

    def test_stale_prune_keeps_an_entry_that_still_has_a_live_waiter(self) -> None:
        # A waiter whose timeout is longer than the stale window must keep its
        # place in the FIFO: pruning it would hand its reply to the next
        # command and report a live transaction as unacknowledged.
        fake = QuietSerial()
        board = MultiboardConnection("COM7", fake)
        board.open()
        board.active_sensor_id = "liquid_flow"
        results: queue.Queue[bool] = queue.Queue()

        with patch("backend.serial_manager.STALE_REPLY_SECONDS", 0.05):

            def slow_wait() -> None:
                results.put(board.send_and_wait_for_ack("DFOFF", timeout=2.0))

            worker = threading.Thread(target=slow_wait, daemon=True)
            worker.start()
            self._await_registration(board)
            # Registering L0 prunes the head of the FIFO on the way in.
            board.send("L0")
            time.sleep(0.15)  # well past the shortened stale window
            self.assertEqual([entry.command for entry in board._ack_queue], ["DFOFF", "L0"])

            fake.reads.put(b"OK\r\n")
            self.assertTrue(results.get(timeout=2.0))
            worker.join(timeout=1.0)
            self.assertEqual([entry.command for entry in board._ack_queue], ["L0"])
        board.close()

    def test_abandoned_entry_is_still_pruned_after_the_stale_window(self) -> None:
        # The stale window itself is kept: an entry whose waiter timed out can
        # be released so a lost reply cannot cascade into later commands.
        fake = QuietSerial()
        board = MultiboardConnection("COM7", fake)
        board.open()
        board.active_sensor_id = "liquid_flow"

        with patch("backend.serial_manager.STALE_REPLY_SECONDS", 0.05):
            self.assertFalse(board.send_and_wait_for_ack("DFOFF", timeout=0.05))
            time.sleep(0.15)
            board.send("L0")
            self.assertEqual([entry.command for entry in board._ack_queue], ["L0"])
        board.close()

    def test_close_clears_the_ack_queue_so_a_reconnect_starts_clean(self) -> None:
        # A reconnect that inherits the previous session's backlog consumes its
        # first acknowledgements: DFOFF then never looks acknowledged and the
        # liquid-flow handshake never reaches STREAMING.
        fake = QuietSerial()
        board = MultiboardConnection("COM7", fake)
        board.open()
        board.active_sensor_id = "liquid_flow"
        for command in ("DFOFF", "L0", "DFON", "P1V120"):
            board.send(command)
        self.assertEqual(len(board._ack_queue), 4)

        board.close()
        self.assertEqual(list(board._ack_queue), [])

        fresh = SensorHandshakeSerial()
        board.connection = fresh
        board.open()
        self.assertEqual(list(board._ack_queue), [])
        self.assertTrue(
            board.initialize_liquid_flow(
                max_attempts=1,
                monitor_stream=False,
                sample_timeout_seconds=1.0,
            )
        )
        self.assertEqual(board.state, "streaming")
        board.close()

    def test_close_releases_a_command_that_is_still_waiting(self) -> None:
        # Teardown must not leave a waiter blocked on a command whose session
        # is over; it reports failure instead of hanging until its timeout.
        fake = QuietSerial()
        board = MultiboardConnection("COM7", fake)
        board.open()
        board.active_sensor_id = "liquid_flow"
        results: queue.Queue[bool] = queue.Queue()

        def wait_forever() -> None:
            results.put(board.send_and_wait_for_ack("DFOFF", timeout=10.0))

        worker = threading.Thread(target=wait_forever, daemon=True)
        worker.start()
        self._await_registration(board)
        board.close()
        self.assertFalse(results.get(timeout=2.0))
        worker.join(timeout=1.0)

    def test_unsolicited_boot_line_does_not_consume_a_pending_command(self) -> None:
        # A board that reboots (or a staggered banner) prints this unasked.
        # It must not eat the FIFO entry of an unrelated in-flight command.
        fake = QuietSerial()
        board = MultiboardConnection("COM7", fake)
        board.open()
        board.active_sensor_id = "liquid_flow"
        results: queue.Queue[bool] = queue.Queue()

        def wait_for_ack() -> None:
            results.put(board.send_and_wait_for_ack("DFON", timeout=2.0))

        worker = threading.Thread(target=wait_for_ack, daemon=True)
        worker.start()
        self._await_registration(board)
        board._handle_line("Multiboard Ready")
        self.assertEqual([entry.command for entry in board._ack_queue], ["DFON"])

        fake.reads.put(b"OK\r\n")
        self.assertTrue(results.get(timeout=2.0))
        worker.join(timeout=1.0)
        self.assertEqual(list(board._ack_queue), [])
        board.close()

    def test_identification_reply_still_consumes_the_firmware_entry(self) -> None:
        # The genuine answer to "V" keeps consuming its own FIFO entry, so the
        # following OK lines stay in order.
        fake = QuietSerial()
        board = MultiboardConnection("COM7", fake)
        board.open()
        board.request_firmware()
        self.assertEqual([entry.command for entry in board._ack_queue], ["V"])

        board._handle_line("Multiboard2 v1.6")
        self.assertEqual(board.firmware, "Multiboard2 v1.6")
        self.assertEqual(list(board._ack_queue), [])

        board.request_firmware()
        board._handle_line("Multiboard Ready")
        self.assertEqual(list(board._ack_queue), [])
        board.close()

    def test_pressure_samples_do_not_satisfy_the_liquid_flow_detection(self) -> None:
        # The liquid-flow two-sample check must only count liquid-flow samples;
        # a pressure stream used to satisfy it and reach STREAMING.
        fake = PressureOnlyHandshakeSerial()
        board = MultiboardConnection("COM7", fake)
        board.open()
        self.assertFalse(
            board.initialize_liquid_flow(
                max_attempts=1,
                monitor_stream=False,
                sample_timeout_seconds=1.0,
            )
        )
        with board._sample_condition:
            self.assertEqual(board._measurement_counts.get("pressure"), 2)
            self.assertEqual(board._measurement_counts.get("liquid_flow", 0), 0)
        events = board.drain_events()
        messages = [event.message for event in events]
        retries = [event.message for event in events if event.kind == "sensor_retry"]
        self.assertIn("no valid flow samples received", retries)
        self.assertNotIn("Liquid-flow sensor connected", messages)
        board.close()

    def test_wait_for_measurements_counts_only_the_requested_sensor(self) -> None:
        fake = QuietSerial()
        board = MultiboardConnection("COM7", fake)
        board.open()
        board.active_sensor_id = "liquid_flow"
        starting_count = board._sample_count("liquid_flow")

        board._handle_line("RSDPC=120.5")
        board._handle_line("RSDPC=121.0")
        self.assertFalse(
            board._wait_for_measurements(
                starting_count, required=2, timeout=0.2, sensor_id="liquid_flow"
            )
        )

        board._handle_line("V=0.0")
        board._handle_line("V=-0.1")
        self.assertTrue(
            board._wait_for_measurements(
                starting_count, required=2, timeout=0.2, sensor_id="liquid_flow"
            )
        )
        board.close()

    def test_unsolicited_banner_does_not_complete_the_firmware_wait(self) -> None:
        # The board announces itself unprompted (power-on, reboot, staggered
        # startup). Such a banner proves nothing about whether an
        # identification query was answered, so it must never complete a
        # firmware wait: the caller would report a version it never received.
        fake = QuietSerial()
        board = MultiboardConnection("COM7", fake)
        board.open()

        # A banner with no identification request outstanding answers nothing.
        board._handle_line("Multiboard Ready")
        self.assertFalse(board._firmware_event.is_set())
        self.assertEqual(board.firmware, "Unknown")

        # A reboot banner while an unrelated command is still unanswered is
        # not the answer to the identification request either: it must leave
        # the request in flight so the wait reports failure on timeout.
        board.send("DFOFF")
        self._await_registration(board)
        results: queue.Queue[bool] = queue.Queue()

        def wait_for_firmware() -> None:
            results.put(board.request_firmware_and_wait(timeout=0.3))

        worker = threading.Thread(target=wait_for_firmware, daemon=True)
        worker.start()
        self._await_registration(board, expected=2)
        fake.reads.put(b"Multiboard Ready\r\n")
        self.assertFalse(results.get(timeout=3.0))
        worker.join(timeout=1.0)
        # The banner answered neither command: both entries are untouched.
        self.assertEqual(
            [entry.command for entry in board._ack_queue], ["DFOFF", "V"]
        )
        board.close()

    def test_identification_reply_completes_the_firmware_wait(self) -> None:
        # The genuine answer to "V" is the only thing that may complete the
        # wait, and it still consumes its own FIFO entry.
        fake = QuietSerial()
        board = MultiboardConnection("COM7", fake)
        board.open()
        results: queue.Queue[bool] = queue.Queue()

        def wait_for_firmware() -> None:
            results.put(board.request_firmware_and_wait(timeout=2.0))

        worker = threading.Thread(target=wait_for_firmware, daemon=True)
        worker.start()
        self._await_registration(board)
        fake.reads.put(b"Multiboard2 v1.6\r\n")
        self.assertTrue(results.get(timeout=3.0))
        worker.join(timeout=1.0)
        self.assertEqual(board.firmware, "Multiboard2 v1.6")
        self.assertEqual(list(board._ack_queue), [])
        board.close()

    def test_pressure_only_samples_cannot_report_liquid_flow_as_streaming(self) -> None:
        # Only liquid flow was initialized, so a pressure measurement must not
        # fabricate a STREAMING state for a sensor that never produced a stream.
        fake = QuietSerial()
        board = MultiboardConnection("COM7", fake)
        board.open()
        board.active_sensor_id = "liquid_flow"
        board._set_state(STREAM_REQUESTED, "Requesting liquid-flow samples…")

        board._handle_line("RSDPC=120.5")
        board._handle_line("RSDPC=121.0")

        self.assertNotEqual(board.state, STREAMING)
        self.assertEqual(board._sample_count("liquid_flow"), 0)
        self.assertEqual(board._sample_count("pressure"), 2)
        # The measurements are still published; only the false state is gone.
        samples = [
            event.sample for event in board.drain_events() if event.sample is not None
        ]
        self.assertEqual([sample.sensor_id for sample in samples], ["pressure", "pressure"])

        # The initialized sensor's own samples do report the stream.
        board._handle_line("V=0.0")
        self.assertEqual(board.state, STREAMING)
        self.assertEqual(board._sample_count("liquid_flow"), 1)
        board.close()

    def test_pressure_only_handshake_never_reports_streaming(self) -> None:
        # End to end: a board that only ever answers with pressure must fail
        # the liquid-flow handshake without ever reporting STREAMING.
        fake = PressureOnlyHandshakeSerial()
        board = MultiboardConnection("COM7", fake)
        board.open()
        observed_states: queue.Queue[str] = queue.Queue()
        board.subscribe_events(lambda _event: observed_states.put(board.state))
        self.assertFalse(
            board.initialize_liquid_flow(
                max_attempts=1,
                monitor_stream=False,
                sample_timeout_seconds=1.0,
            )
        )
        seen_states = []
        while True:
            try:
                seen_states.append(observed_states.get_nowait())
            except queue.Empty:
                break
        self.assertNotIn(STREAMING, seen_states)
        self.assertNotEqual(board.state, STREAMING)
        self.assertEqual(board._sample_count("liquid_flow"), 0)
        self.assertEqual(board._sample_count("pressure"), 2)
        board.close()

    def test_unframed_receive_run_is_bounded_and_fabricates_no_line(self) -> None:
        # A device that stops emitting newlines (a partial write, or a
        # corrupted/looping byte stream) used to make the reader buffer keep
        # every byte for as long as the port stayed open.
        run = _NOISE_UNIT * ((MAX_PENDING_LINE_BYTES + 1024) // len(_NOISE_UNIT))
        stop_event = threading.Event()
        fake = ScriptedReadSerial(
            [run, b"\n", b"V=2.5\r\n", b"OK\r\n"], stop_event
        )
        board = MultiboardConnection("COM7", fake)
        board._stop_event = stop_event
        board.active_sensor_id = "liquid_flow"
        # Two commands stay outstanding across the garbage run, so the
        # resynchronisation is also checked against the ack FIFO.
        board.send("DFOFF")
        board.send("DFON")
        board.drain_events()

        board._reader_loop()

        # The buffer never grew past the constant: the run past the cap was
        # discarded whole rather than accumulated.
        self.assertEqual(board.rx_buffer_overflows, 1)
        self.assertEqual(board.discarded_rx_bytes, len(run))
        events = board.drain_events()
        # Nothing from the discarded run reached the line handler, so it
        # fabricated no measurement: the only sample is the real line that
        # followed the resynchronisation.
        self.assertEqual(board._sample_count("liquid_flow"), 1)
        samples = [event.sample for event in events if event.sample is not None]
        self.assertEqual([sample.raw_line for sample in samples], ["V=2.5"])
        # It consumed no ack FIFO entry either: the OK that terminated the run
        # still acknowledged the oldest outstanding command (DFOFF -> "Sensor
        # stream stopped"), leaving DFON queued, instead of being eaten by the
        # discarded run or shifting later replies by one.
        self.assertEqual(board.state, "ready")
        self.assertEqual([entry.command for entry in board._ack_queue], ["DFON"])
        # And the loss is reported, not silently swallowed.
        overflow = [event for event in events if event.kind == "rx_overflow"]
        self.assertEqual(len(overflow), 1)
        self.assertIn(str(len(run)), overflow[0].message)

    def test_receive_buffer_at_the_cap_is_still_framed_as_one_line(self) -> None:
        # The bound triggers only when the pending run *exceeds* the cap, so a
        # line of exactly MAX_PENDING_LINE_BYTES is delivered intact instead of
        # being discarded - and, being unparseable noise, it still fabricates no
        # measurement and consumes no ack FIFO entry.
        run = _NOISE_UNIT * (MAX_PENDING_LINE_BYTES // len(_NOISE_UNIT))
        self.assertEqual(len(run), MAX_PENDING_LINE_BYTES)
        stop_event = threading.Event()
        fake = ScriptedReadSerial([run, b"\n", b"V=2.5\r\n"], stop_event)
        board = MultiboardConnection("COM7", fake)
        board._stop_event = stop_event
        board.active_sensor_id = "liquid_flow"
        board.send("DFOFF")
        board.drain_events()

        board._reader_loop()

        self.assertEqual(board.rx_buffer_overflows, 0)
        self.assertEqual(board.discarded_rx_bytes, 0)
        self.assertEqual(board._sample_count("liquid_flow"), 1)
        # The unparseable run was offered to the parser but matched nothing,
        # so the pending DFOFF is still the only outstanding command.
        self.assertEqual([entry.command for entry in board._ack_queue], ["DFOFF"])

    def test_long_but_framed_line_is_parsed_and_does_not_resynchronise(self) -> None:
        # Guard against over-correction: a line far longer than any real
        # Multiboard2 reply, but still within the generous cap, must be parsed
        # normally - including when it spans several reads.
        payload = "0" * (MAX_PENDING_LINE_BYTES - 24)
        diagnostic = f"[E][Wire.cpp:234] error: i2c bus timeout: {payload}"
        wire = f"{diagnostic}\r\n".encode("ascii")
        split = len(wire) // 3
        stop_event = threading.Event()
        fake = ScriptedReadSerial(
            [wire[:split], wire[split : 2 * split], wire[2 * split :], b"V=1.5\r\n"],
            stop_event,
        )
        board = MultiboardConnection("COM7", fake)
        board._stop_event = stop_event
        board.active_sensor_id = "liquid_flow"
        board.send("DFOFF")
        board.drain_events()

        board._reader_loop()

        self.assertEqual(board.rx_buffer_overflows, 0)
        self.assertEqual(board.discarded_rx_bytes, 0)
        events = board.drain_events()
        self.assertIn(
            diagnostic, [event.message for event in events if event.kind == "diagnostic"]
        )
        samples = [event.sample for event in events if event.sample is not None]
        self.assertEqual([sample.value for sample in samples], [1500.0])
        self.assertEqual(board.last_rx_line, "V=1.5")

    def test_event_queue_is_bounded_and_counts_shed_events(self) -> None:
        # A stalled or absent consumer (sensors page closed or rebuilding) must
        # not let the published-event queue grow with every parsed measurement.
        with patch("backend.serial_manager.EVENT_QUEUE_LIMIT", 16):
            board = MultiboardConnection("COM7", QuietSerial())
        limit = board.events.maxsize
        self.assertEqual(limit, 16)
        board.open()
        board.active_sensor_id = "liquid_flow"
        board.drain_events()
        # Non-consuming subscribers must not be affected by the drop policy.
        seen: list[BackendEvent] = []
        board.subscribe_events(seen.append)

        lines = 64
        for index in range(lines):
            board._handle_line(f"V={index}.5")

        # A streamed measurement publishes three events (raw, STREAMING state,
        # measurement); the queue keeps exactly its bound and sheds the rest.
        total = lines * 3
        self.assertGreater(total, limit)
        self.assertEqual(board.events.qsize(), limit)
        self.assertEqual(board.dropped_events, total - limit)
        self.assertEqual(len(seen), total)
        # Drop-oldest: the retained window is exactly the newest `limit`
        # published events, so a consumer that resumes sees current board state
        # instead of a replay of the backlog.
        drained = board.drain_events(limit=limit)
        self.assertEqual(drained, seen[-limit:])
        samples = [event.sample.value for event in drained if event.sample is not None]
        self.assertEqual(samples[-1], 1000.0 * (lines - 1) + 500.0)
        board.close()

    def test_normal_event_burst_sheds_nothing(self) -> None:
        # The bound is a runaway guard, not a budget: an ordinary burst of
        # samples at the shipped limit must pass through untouched.
        fake = QuietSerial()
        board = MultiboardConnection("COM7", fake)
        board.open()
        board.active_sensor_id = "liquid_flow"
        board.drain_events()

        lines = 1000
        for index in range(lines):
            board._handle_line(f"V={index}.5")

        self.assertEqual(board.dropped_events, 0)
        drained = board.drain_events(limit=lines * 3)
        self.assertEqual(len(drained), lines * 3)
        self.assertEqual(len([event for event in drained if event.sample is not None]), lines)
        self.assertLess(len(drained), EVENT_QUEUE_LIMIT)
        board.close()


if __name__ == "__main__":
    unittest.main()
