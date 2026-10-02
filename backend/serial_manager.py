"""Threaded, UI-independent Multiboard2 serial backend.

The connection object owns one UART reader thread, serializes writes, parses
replies, publishes thread-safe ``BackendEvent`` objects, and performs the sensor
initialization/streaming state machine.

Reply correlation: the Multiboard2 acknowledges every command with a bare
``OK``/``FAIL`` line, so each outgoing command is registered in a FIFO queue
and every incoming ack/error reply is attributed to the oldest outstanding
command (boards answer in wire order). A command whose reply timed out stays
in the queue, so its late reply is consumed correctly instead of being
mistaken for the acknowledgement of the next command; such an entry is marked
abandoned and may be released after ``STALE_REPLY_SECONDS``, while an entry
whose waiter is still blocked is never pruned. Conversely, a command whose
bytes never reached the wire (transport failure or teardown gate) is
unregistered immediately, because nothing will ever answer it.

Identification and streaming follow the same rule. The identification request
is the one command answered with version text instead of ``OK``, so an
unsolicited power-on/reboot banner is never treated as its answer: it neither
completes a firmware wait nor consumes another command's FIFO entry. Likewise
only the sensor this session actually initialized may report the board as
streaming; a measurement from any other sensor is still published, but it
cannot fabricate a STREAMING state.

Both queues are bounded. Bytes that never reach a newline are discarded past
``MAX_PENDING_LINE_BYTES`` and the reader resynchronises on the next delimiter,
so a device that stops framing cannot leak memory - and because the discarded
run is dropped whole, it is never handed to the line handler as if it were a
protocol reply, which would let it consume an ack FIFO entry. The published
event queue is capped at ``EVENT_QUEUE_LIMIT``: overflow sheds the oldest
event and counts it in ``dropped_events``. Neither loss is silent.

See ``docs/DEVELOPER_GUIDE.md`` for command/reply framing and
``docs/DEVELOPER_GUIDE.md`` for the sensor data path.
"""

from __future__ import annotations

import queue
import statistics
import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from time import monotonic, sleep
from typing import Any

try:
    import serial
except ModuleNotFoundError:
    serial = None  # type: ignore[assignment]

from backend.protocol import (
    CALIBRATION_COMMANDS,
    SENSORS,
    Calibration,
    ParsedReply,
    encode_command,
    parse_reply,
)
BAUD_RATE = 115_200
# A stream gap longer than this is treated as an unknown interval (no volume
# integration) unless the sensor's own recent sample rate justifies a longer
# continuous window. See _integrate_flow_sample_locked().
BASE_FLOW_GAP_SECONDS = 2.0
FLOW_GAP_RATE_MULTIPLIER = 3.0
FLOW_INTERVAL_HISTORY = 20
# Gaps longer than this are clearly stalls, not slow sample rates, and are
# excluded from the cadence estimate used by the adaptive gap guard.
FLOW_STALL_RECORD_SECONDS = 10.0
# Commands whose reply never arrived stop correlating replies after this
# window, so a "no reply" command cannot swallow later acknowledgements. Only
# entries nobody waits for any more may be released this way.
STALE_REPLY_SECONDS = 5.0
# Longest run of received bytes that may wait for its terminating newline.
# A complete Multiboard2 line is a short measurement, acknowledgement or
# firmware banner of at most a few dozen bytes, so this is thousands of times
# the longest plausible reply (comfortably containing the longest ESP32 Wire
# diagnostic). It exists only so a device that stops emitting newlines - a
# partial write, a corrupted or looping byte stream - cannot make the reader's
# pending buffer, and therefore the process, grow without limit. At the 115200
# baud link rate the cap is reached only after roughly 5.7 s of unframed data.
MAX_PENDING_LINE_BYTES = 64 * 1024
# Bounds the published-event queue so a stalled or absent consumer (the sensors
# page closed, mid-rebuild, or blocked) cannot grow it with every parsed
# measurement. Overflow sheds the *oldest* queued event rather than the newest,
# so a consumer that resumes sees current board state instead of a replay of
# stale samples - the same policy AsyncCsvLogger.submit() already uses.
# Each parsed line publishes a "raw" plus one typed event and the UI drains ten
# times a second, so 20 000 slots is well over a hundred seconds of backlog:
# a normal stall never reaches it, and memory stays bounded at a few MiB when
# nobody drains at all.
EVENT_QUEUE_LIMIT = 20_000
# Board identification request. It is the one command answered with version
# text instead of ``OK``, so an unsolicited version/boot banner must never be
# attributed to it, nor may it consume any other command's FIFO entry.
IDENTIFICATION_COMMAND = "V"
# These states mirror the useful part of FluidicStudio's connection flow:
# connect the board, identify it, configure the selected sensor, then stream.
DISCONNECTED = "disconnected"
OPENING = "opening"
HANDSHAKE = "handshake"
READY = "ready"
CALIBRATING = "calibrating"
STREAM_REQUESTED = "stream_requested"
STREAMING = "streaming"
CLOSING = "closing"
ERROR = "error"


@dataclass(frozen=True)
class SensorSample:
    timestamp: datetime
    elapsed_seconds: float
    board_port: str
    sensor_id: str
    value: float
    unit: str
    accumulated_volume_ul: float | None
    raw_line: str
    raw_value_ml_min: float | None = None


@dataclass(frozen=True)
class BackendEvent:
    kind: str
    board_port: str
    message: str = ""
    reply: ParsedReply | None = None
    sample: SensorSample | None = None


@dataclass(frozen=True)
class CommandSequenceResult:
    """Result of one acknowledged serial transaction."""

    success: bool
    commands: tuple[str, ...]
    failed_command: str | None = None
    rollback_command: str | None = None
    rollback_succeeded: bool | None = None
    message: str = ""


@dataclass
class _AckEntry:
    """One outgoing command awaiting its in-order board reply."""

    command: str
    sent_at: float
    event: threading.Event | None = None
    succeeded: bool | None = None
    # Set once the waiter gave up. Only abandoned (or waiter-less) entries may
    # be pruned; a still-blocked waiter keeps its place at the head of the FIFO.
    abandoned: bool = False


class MultiboardConnection:
    """Own one COM port and publish parsed events through a thread-safe queue."""

    def __init__(
        self,
        port: str,
        connection: Any | None = None,
    ) -> None:
        self.port = port
        self.connection = connection
        self.events: queue.Queue[BackendEvent] = queue.Queue(maxsize=EVENT_QUEUE_LIMIT)
        # Published events lost to a stalled or absent consumer. Counted, never
        # silent: a full queue means the UI is not draining, and the gap has to
        # be visible instead of reading as a quiet board.
        self._dropped_events = 0
        # Receive bytes thrown away because the pending run exceeded
        # MAX_PENDING_LINE_BYTES, and how many separate times that happened.
        self._discarded_rx_bytes = 0
        self._rx_buffer_overflows = 0
        # Optional read-only event subscribers provide fan-out for compact
        # Workspace views while preserving the original drain_events() queue.
        # Callbacks run on the producer thread and therefore must stay fast.
        self._event_subscribers = set()
        self._event_subscribers_lock = threading.Lock()
        self.active_sensor_id: str | None = None
        self.firmware = "Unknown"
        self.state = DISCONNECTED
        self.last_rx_line = ""
        self.last_command = ""
        self._opened_at = monotonic()
        self._stop_event = threading.Event()
        self._write_lock = threading.Lock()
        # Orders command writes and keeps the ack FIFO consistent with wire
        # order. Waiting for a reply happens outside this lock so a slow
        # board cannot block other senders for the full timeout.
        self._command_lock = threading.RLock()
        self._ack_lock = threading.Lock()
        self._ack_queue: deque[_AckEntry] = deque()
        # Teardown barrier. Once close() starts, no further command bytes may
        # reach the wire: otherwise an in-flight transaction (e.g. a pump
        # start) could emit its ON command *after* the global POFF and leave
        # the board driving a channel the app believes is off.
        self._accepting_commands = True
        self._firmware_event = threading.Event()
        self._sample_condition = threading.Condition()
        # Parsed measurements counted per sensor id, so a wait for one sensor
        # cannot be satisfied by another sensor's stream. Guarded by
        # ``_sample_condition``.
        self._measurement_counts: dict[str, int] = {}
        # Guards flow integration state shared between the reader thread and
        # threads that reset it (stop_sensor, close, watchdog retry).
        self._flow_lock = threading.Lock()
        self._last_flow_time: float | None = None
        self._last_flow_value: float | None = None
        self._volume_ul = 0.0
        self._recent_flow_intervals: deque[float] = deque(maxlen=FLOW_INTERVAL_HISTORY)
        self._initialization_cancel = threading.Event()
        self._initializer: threading.Thread | None = None
        self._reader: threading.Thread | None = None

    @property
    def is_open(self) -> bool:
        return bool(self.connection is not None and getattr(self.connection, "is_open", False))

    @property
    def dropped_events(self) -> int:
        """Published events shed because the bounded event queue was full."""

        return self._dropped_events

    @property
    def discarded_rx_bytes(self) -> int:
        """Receive bytes dropped because no newline arrived in time."""

        return self._discarded_rx_bytes

    @property
    def rx_buffer_overflows(self) -> int:
        """How often the receive buffer resynchronised on an oversized run."""

        return self._rx_buffer_overflows

    @property
    def accumulated_volume_ul(self) -> float:
        with self._flow_lock:
            return self._volume_ul

    def open(self) -> None:
        if self.is_open and self._reader is not None and self._reader.is_alive():
            return
        self._set_state(OPENING, "Opening Multiboard serial connection")
        if not self.is_open:
            self.connection = None
        if self.connection is None:
            if serial is None:
                raise RuntimeError(
                    "pyserial is not installed. Run: python -m pip install -r requirements.txt"
                )
            self.connection = serial.Serial(
                port=self.port,
                baudrate=BAUD_RATE,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=0.1,
                write_timeout=1.0,
            )
        self._opened_at = monotonic()
        self._stop_event.clear()
        self._accepting_commands = True
        self._internal_write = False
        self._release_pending_ack_entries()
        # The previous session's identification answer belongs to that session:
        # a reconnect must not look identified before it asked.
        self._firmware_event.clear()
        self._reader = threading.Thread(
            target=self._reader_loop,
            name=f"multiboard-reader-{self.port}",
            daemon=True,
        )
        # FluidicStudio starts from a clean receive buffer.  A reset/reconnect
        # can otherwise leave an old partial response in front of the first V
        # reply and make the following DFON sequence appear to fail.
        reset_input = getattr(self.connection, "reset_input_buffer", None)
        if callable(reset_input):
            reset_input()
        self._reader.start()
        self._set_state(HANDSHAKE, "Serial connection open; waiting for firmware identification")
        self._publish_event(BackendEvent("connected", self.port))

    def _register_ack_entry(self, command: str, event: threading.Event | None) -> _AckEntry:
        """Record one outgoing command for in-order reply attribution."""

        now = monotonic()
        entry = _AckEntry(command=command, sent_at=now, event=event)
        with self._ack_lock:
            self._prune_stale_locked(now)
            self._ack_queue.append(entry)
        return entry

    def _prune_stale_locked(self, now: float) -> None:
        # Only entries nobody can be waiting for anymore are released. Pruning
        # a live waiter would hand its reply to the next command, which would
        # then look unacknowledged while every later command shifts by one.
        while self._ack_queue:
            head = self._ack_queue[0]
            if now - head.sent_at <= STALE_REPLY_SECONDS:
                break
            if head.event is not None and not head.abandoned:
                break
            self._ack_queue.popleft()

    def _discard_ack_entry(self, entry: _AckEntry) -> None:
        """Remove exactly one registered command from the FIFO (by identity)."""

        with self._ack_lock:
            for index, candidate in enumerate(self._ack_queue):
                if candidate is entry:
                    del self._ack_queue[index]
                    return

    def _abandon_ack_entry(self, entry: _AckEntry) -> None:
        """Record that the waiter for this command gave up."""

        with self._ack_lock:
            entry.abandoned = True

    def _release_pending_ack_entries(self) -> None:
        """Drop the session's outstanding commands and release their waiters.

        A reconnect must not inherit the previous session's backlog: the stale
        entries would consume the first acknowledgements of the new session and
        make a healthy board look unresponsive forever.
        """

        with self._ack_lock:
            pending = list(self._ack_queue)
            self._ack_queue.clear()
        for entry in pending:
            entry.succeeded = False
            if entry.event is not None:
                entry.event.set()

    def _require_open_for_write(self) -> None:
        """Raise if teardown has begun, so no bytes reach a closing board."""

        if not self._accepting_commands and not self._internal_write:
            raise RuntimeError(f"{self.port} is closing; command not sent")

    def _write_command_locked(self, entry: _AckEntry, payload: bytes) -> None:
        """Put one registered command on the wire, or unregister it again.

        A rejected write (serial exception, write timeout, teardown gate) will
        never be answered by the board, so its FIFO entry has to be removed
        before the exception propagates. Otherwise the next command's ``OK`` is
        consumed by the dead entry and the live command is reported as
        unacknowledged.
        """

        try:
            with self._write_lock:
                self._require_open_for_write()
                self.last_command = entry.command
                self.connection.write(payload)
                self.connection.flush()
        except BaseException:
            self._discard_ack_entry(entry)
            raise

    def _pop_reply_target_locked(self, now: float) -> _AckEntry | None:
        self._prune_stale_locked(now)
        if not self._ack_queue:
            return None
        return self._ack_queue.popleft()

    def send(self, command: str) -> None:
        # Command syntax belongs in backend.protocol; transport/framing and
        # acknowledgement behavior are documented in docs/DEVELOPER_GUIDE.md.
        if not self.is_open:
            raise RuntimeError(f"{self.port} is not open")
        payload = encode_command(command)
        clean = command.strip()
        with self._command_lock:
            entry = self._register_ack_entry(clean, None)
            self._write_command_locked(entry, payload)
        self._publish_event(BackendEvent("command", self.port, clean))

    def send_and_wait_for_ack(self, command: str, timeout: float = 1.0) -> bool:
        """Send one command and require the board's ``OK`` response.

        The reply is correlated through the ack FIFO, so a reply that arrives
        late can no longer be mistaken for the acknowledgement of a later
        command. The wait happens outside ``_command_lock``; a slow board
        therefore cannot block unrelated senders for the full timeout.
        """

        clean = command.strip()
        if not self.is_open:
            raise RuntimeError(f"{self.port} is not open")
        event = threading.Event()
        with self._command_lock:
            entry = self._register_ack_entry(clean, event)
            self._write_command_locked(entry, encode_command(clean))
        self._publish_event(BackendEvent("command", self.port, clean))
        if not event.wait(max(0.0, float(timeout))):
            # Nobody is blocked on this entry any more, so the stale window may
            # release it later; until then a late reply stays attributed here.
            self._abandon_ack_entry(entry)
            return False
        return entry.succeeded is True

    def send_sequence(
        self,
        commands,
        *,
        timeout: float = 1.0,
        rollback_command: str | None = None,
    ) -> CommandSequenceResult:
        """Send an acknowledged transaction with caller-supplied rollback.

        Reply attribution is handled per command through the ack FIFO, so
        unrelated senders may interleave at the transport level without
        corrupting acknowledgement ownership. A rollback is deliberately
        caller-supplied; pump code uses only the affected channel's OFF
        command rather than a global POFF.
        """

        sequence = tuple(str(command).strip() for command in commands if str(command).strip())
        if not sequence:
            return CommandSequenceResult(True, ())

        for command in sequence:
            try:
                acknowledged = self.send_and_wait_for_ack(command, timeout=timeout)
            except Exception as exc:
                acknowledged = False
                failure_message = f"{command} failed: {exc}"
            else:
                failure_message = f"{command} was not acknowledged"
            if acknowledged:
                continue

            rollback_succeeded = None
            clean_rollback = rollback_command.strip() if rollback_command else None
            if clean_rollback:
                try:
                    rollback_succeeded = self.send_and_wait_for_ack(
                        clean_rollback, timeout=timeout
                    )
                except Exception:
                    rollback_succeeded = False
            return CommandSequenceResult(
                success=False,
                commands=sequence,
                failed_command=command,
                rollback_command=clean_rollback,
                rollback_succeeded=rollback_succeeded,
                message=failure_message,
            )

        return CommandSequenceResult(True, sequence, message="Transaction acknowledged")

    def request_firmware(self) -> None:
        """Ask the board to identify itself, without waiting for the answer."""

        self._request_identification(None)

    def request_firmware_and_wait(self, timeout: float = 2.0) -> bool:
        """Request identification and wait for the answer to *this* request.

        Only version/boot text that answers an outstanding identification
        request completes the wait. The board also announces itself unprompted
        (power-on, reboot, staggered startup), and such a banner proves nothing
        about whether the query was answered — the caller would report a
        firmware version it never received.

        The entry is registered with the completion event, so the ack FIFO keeps
        it for as long as this thread waits (a live waiter is never pruned) and
        releases it exactly like an acknowledged command. The wait itself
        happens outside ``_command_lock``.
        """

        self._firmware_event.clear()
        entry = self._request_identification(self._firmware_event)
        if not self._firmware_event.wait(max(0.0, float(timeout))):
            # Nobody is blocked on this entry any more, so the stale window may
            # release it later; until then a late banner stays attributed here.
            self._abandon_ack_entry(entry)
            return False
        return entry.succeeded is True

    def _request_identification(self, event: threading.Event | None) -> _AckEntry:
        """Put one identification request on the wire; returns its FIFO entry."""

        self._set_state(HANDSHAKE, "Requesting Multiboard firmware")
        with self._command_lock:
            entry = self._register_ack_entry(IDENTIFICATION_COMMAND, event)
            self._write_command_locked(entry, encode_command(IDENTIFICATION_COMMAND))
        self._publish_event(BackendEvent("command", self.port, IDENTIFICATION_COMMAND))
        return entry

    def attach_initializer(self, thread: threading.Thread) -> None:
        self._initializer = thread

    def _sample_count(self, sensor_id: str) -> int:
        """Parsed measurements seen so far for one sensor id."""

        with self._sample_condition:
            return self._measurement_counts.get(sensor_id, 0)

    def _record_measurement_locked(self, sensor_id: str) -> None:
        """Count one parsed measurement; must hold ``_sample_condition``."""

        self._measurement_counts[sensor_id] = self._measurement_counts.get(sensor_id, 0) + 1
        self._sample_condition.notify_all()

    def _wait_for_measurements(
        self,
        starting_count: int,
        required: int,
        timeout: float,
        sensor_id: str,
    ) -> bool:
        """Wait for ``required`` further samples of exactly ``sensor_id``.

        ``starting_count`` is the count observed for that sensor before the
        stream command was sent, so only samples that arrive during this wait
        can satisfy the requirement. Samples from other sensors are ignored:
        a pressure stream must never prove that the liquid-flow sensor exists.
        """

        deadline = monotonic() + max(0.0, float(timeout))
        with self._sample_condition:
            while self._measurement_counts.get(sensor_id, 0) - starting_count < required:
                remaining = deadline - monotonic()
                if remaining <= 0 or self._initialization_cancel.is_set():
                    return False
                self._sample_condition.wait(min(remaining, 0.25))
            return True

    def initialize_liquid_flow(
        self,
        *,
        retry_delay_seconds: float = 2.0,
        sample_timeout_seconds: float = 3.0,
        max_attempts: int | None = None,
        monitor_stream: bool = True,
    ) -> bool:
        """Synchronize and detect the flow sensor without blocking Qt.

        A sensor is considered present only after two valid finite samples.
        Their numeric value is irrelevant: zero and negative values are valid.
        A watchdog retry preserves the accumulated volume of the running
        session; only a deliberately restarted stream resets it.
        """

        self._initialization_cancel.clear()
        attempt = 0
        while self.is_open and not self._initialization_cancel.is_set():
            attempt += 1
            self._set_state(HANDSHAKE, "Checking liquid-flow sensor…")
            try:
                if not self.request_firmware_and_wait(timeout=2.0):
                    raise RuntimeError("board identification timed out")
                if not self.send_and_wait_for_ack("DFOFF", timeout=1.5):
                    raise RuntimeError("DFOFF was not acknowledged")
                if not self.send_and_wait_for_ack(
                    CALIBRATION_COMMANDS[Calibration.WATER], timeout=1.5
                ):
                    raise RuntimeError("L0 calibration was not acknowledged")

                sensor_id = "liquid_flow"
                self.active_sensor_id = sensor_id
                self._reset_integration(keep_total=True)
                with self._sample_condition:
                    starting_count = self._sample_count(sensor_id)
                self._set_state(STREAM_REQUESTED, "Requesting liquid-flow samples…")
                if not self.send_and_wait_for_ack(
                    SENSORS[sensor_id].start_command, timeout=1.5
                ):
                    raise RuntimeError("DFON was not acknowledged")
                if self._wait_for_measurements(
                    starting_count,
                    required=2,
                    timeout=sample_timeout_seconds,
                    sensor_id=sensor_id,
                ):
                    self._set_state(STREAMING, "Liquid-flow sensor connected")
                    if not monitor_stream:
                        return True
                    # Stay alive as a lightweight watchdog. If valid lines
                    # stop for five seconds, run the complete acknowledged
                    # DFOFF/L0/DFON sequence again instead of retrying DFON
                    # once and waiting forever.
                    with self._sample_condition:
                        observed_count = self._sample_count(sensor_id)
                    while self.is_open and not self._initialization_cancel.is_set():
                        with self._sample_condition:
                            self._sample_condition.wait(timeout=5.0)
                            current_count = self._sample_count(sensor_id)
                        if current_count != observed_count:
                            observed_count = current_count
                            continue
                        self._publish_event(
                            BackendEvent(
                                "sensor_retry",
                                self.port,
                                "flow stream paused",
                            )
                        )
                        break
                    if self._initialization_cancel.is_set() or not self.is_open:
                        return True
                    continue
                raise RuntimeError("no valid flow samples received")
            except Exception as exc:
                if self._initialization_cancel.is_set() or not self.is_open:
                    return False
                self.active_sensor_id = None
                self._publish_event(BackendEvent("sensor_retry", self.port, str(exc)))
                self._set_state(READY, "Sensor not detected — retrying…")

            if max_attempts is not None and attempt >= max(1, int(max_attempts)):
                return False
            self._initialization_cancel.wait(max(0.1, float(retry_delay_seconds)))
        return False

    def start_sensor(
        self,
        sensor_id: str,
        calibration: Calibration = Calibration.WATER,
        calibration_delay_seconds: float = 0.0,
    ) -> None:
        if sensor_id not in SENSORS:
            raise ValueError(f"Unknown sensor: {sensor_id}")
        if self.active_sensor_id and self.active_sensor_id != sensor_id:
            self.stop_sensor()
        if sensor_id == "liquid_flow":
            self._set_state(CALIBRATING, "Selecting water calibration (L0)")
            self.send(CALIBRATION_COMMANDS[calibration])
            # The tested Multiboard firmware acknowledges calibration before it
            # reliably accepts the stream command.  Keep the generic backend
            # fast by default, but let the hardware handshake mirror the
            # proven diagnostic sequence.
            sleep(max(0.0, calibration_delay_seconds))
        self.active_sensor_id = sensor_id
        self._reset_integration()
        self._set_state(STREAM_REQUESTED, f"Requesting {sensor_id} stream ({SENSORS[sensor_id].start_command})")
        self.send(SENSORS[sensor_id].start_command)

    def stop_sensor(self) -> None:
        sensor_id = self.active_sensor_id
        if sensor_id and self.is_open:
            self._set_state(CLOSING, f"Stopping {sensor_id} stream")
            self.send(SENSORS[sensor_id].stop_command)
        self.active_sensor_id = None
        self._reset_integration(keep_total=True)

    def retry_active_stream(self) -> bool:
        """Re-send the active stream command without resetting session state.

        Multiboard2 can occasionally acknowledge setup but delay or miss the
        first stream command.  A retry must not recalibrate the sensor or reset
        accumulated volume, because either action would corrupt a running
        measurement session.
        """

        sensor_id = self.active_sensor_id
        if not sensor_id or not self.is_open:
            return False
        self.send(SENSORS[sensor_id].start_command)
        return True

    def reset_accumulated_volume(self) -> None:
        with self._flow_lock:
            self._volume_ul = 0.0
            self._last_flow_time = None
            self._last_flow_value = None
            self._recent_flow_intervals.clear()

    def subscribe_events(self, callback) -> None:
        """Receive a read-only copy of future backend events without draining them."""
        if not callable(callback):
            raise TypeError("event subscriber must be callable")
        with self._event_subscribers_lock:
            self._event_subscribers.add(callback)

    def unsubscribe_events(self, callback) -> None:
        with self._event_subscribers_lock:
            self._event_subscribers.discard(callback)

    def _publish_event(self, event: BackendEvent) -> None:
        """Publish to the legacy queue and to non-consuming subscribers."""
        try:
            self.events.put_nowait(event)
        except queue.Full:
            # Bounded-queue policy: shed the oldest pending event rather than
            # growing memory without limit while nobody drains the queue. The
            # loss is counted and the newest event still lands, so a consumer
            # that resumes continues from current board state.
            try:
                self.events.get_nowait()
            except queue.Empty:
                # A concurrent drain_events() emptied the queue first.
                pass
            else:
                self._dropped_events += 1
            try:
                self.events.put_nowait(event)
            except queue.Full:
                # Shed an event the consumer never got; count it so the loss is
                # not invisible.
                self._dropped_events += 1
        with self._event_subscribers_lock:
            subscribers = tuple(self._event_subscribers)
        for callback in subscribers:
            try:
                callback(event)
            except Exception:
                # Observers are diagnostics/view consumers and must never be
                # able to kill the serial reader or alter transport behavior.
                continue

    def drain_events(self, limit: int = 1000) -> list[BackendEvent]:
        drained = []
        for _ in range(limit):
            try:
                drained.append(self.events.get_nowait())
            except queue.Empty:
                break
        return drained

    def _reader_loop(self) -> None:
        buffer = bytearray()
        try:
            while not self._stop_event.is_set():
                chunk = self.connection.read(256)
                if not chunk:
                    continue
                buffer.extend(chunk)
                while b"\n" in buffer:
                    end = buffer.index(b"\n") + 1
                    raw = bytes(buffer[:end])
                    del buffer[:end]
                    self._handle_line(raw.decode("utf-8", errors="replace").rstrip("\r\n"))
                if len(buffer) > MAX_PENDING_LINE_BYTES:
                    self._resynchronise_receive_buffer(buffer)
        except BaseException as exc:
            if not self._stop_event.is_set():
                self._set_state(ERROR, f"Serial reader failed: {exc}")
                self._publish_event(BackendEvent("error", self.port, str(exc)))
        finally:
            if buffer and not self._stop_event.is_set():
                self._publish_event(
                    BackendEvent("unknown", self.port, buffer.decode("utf-8", errors="replace"))
                )

    def _resynchronise_receive_buffer(self, buffer: bytearray) -> None:
        """Drop an unframed run that exceeded ``MAX_PENDING_LINE_BYTES``.

        No newline arrived for more bytes than any real reply could occupy, so
        the pending data is noise: a device that stopped framing, a partial
        write, or a corrupted/looping byte stream. Keeping it would let the
        buffer - and the process - grow for as long as the port stays open.

        The run is dropped *whole* rather than trimmed, and framing then resumes
        at the next delimiter. That is what keeps it safe for the ack FIFO: no
        part of the discarded run is ever handed to ``_handle_line``, so it
        cannot be parsed as an ``OK`` and consume an outstanding command's
        reply. It also means the first real line after the corruption burst is
        parsed normally instead of being mangled by a retained garbage prefix.
        The only loss is a single line longer than the cap, which no plausible
        Multiboard2 reply is.
        """

        discarded = len(buffer)
        buffer.clear()
        self._discarded_rx_bytes += discarded
        self._rx_buffer_overflows += 1
        # Reported as its own event kind so neither the connection state machine
        # nor an awaiting firmware/measurement wait is disturbed by it, and the
        # loss is visible in the event stream instead of silent.
        self._publish_event(
            BackendEvent(
                "rx_overflow",
                self.port,
                f"Discarded {discarded} unframed receive bytes "
                f"(no newline within {MAX_PENDING_LINE_BYTES} bytes); "
                "resynchronised on the next line",
            )
        )

    def _resolve_reply_target(self, now: float) -> _AckEntry | None:
        """Pop the oldest outstanding command for the incoming OK/FAIL reply."""

        with self._ack_lock:
            return self._pop_reply_target_locked(now)

    def _resolve_identification_target(self, now: float) -> _AckEntry | None:
        """Resolve the identification command's entry, if it is still queued.

        Version/boot text is only the answer to the identification request, so
        it consumes a FIFO entry only while that request is the oldest
        outstanding command, and returns that entry so the caller can tell a
        real answer from an unsolicited banner (board reboot, staggered
        startup message). An unsolicited banner must leave an unrelated
        in-flight command — and its waiter — untouched, and must not complete a
        firmware wait.
        """

        with self._ack_lock:
            self._prune_stale_locked(now)
            if not self._ack_queue:
                return None
            head = self._ack_queue[0]
            if head.command != IDENTIFICATION_COMMAND:
                return None
            entry = self._ack_queue.popleft()
            entry.succeeded = True
            if entry.event is not None:
                entry.event.set()
            return entry

    def _handle_line(self, line: str) -> None:
        self.last_rx_line = line
        # Keep the original line visible to the UI/diagnostic log.  Parsing is
        # deliberately separate, so an unfamiliar firmware response cannot
        # silently look like a dead serial connection.
        self._publish_event(BackendEvent("raw", self.port, line))
        reply = parse_reply(line, self.active_sensor_id)
        if reply.kind == "firmware":
            self.firmware = reply.message
            # The identification request is answered with version text, not
            # with OK; consume its FIFO entry so later OKs stay in order. Only
            # a correlated answer releases a pending firmware wait.
            self._resolve_identification_target(monotonic())
            self._set_state(READY, f"Firmware identified: {reply.message}")
        elif reply.kind == "boot":
            # A boot/version line answers the identification request, but only
            # while that request is the oldest outstanding command. Resolution
            # (not the line itself) is what releases the firmware wait.
            self._resolve_identification_target(monotonic())
        elif reply.kind == "error":
            # Only fail the acknowledged command this error actually belongs
            # to; an unsolicited board error must not reject an unrelated
            # in-flight transaction.
            entry = self._resolve_reply_target(monotonic())
            if entry is not None and entry.event is not None:
                entry.succeeded = False
                entry.event.set()
            self._set_state(ERROR, f"Board rejected command: {reply.message}")
        elif reply.kind == "ack":
            entry = self._resolve_reply_target(monotonic())
            if entry is not None:
                entry.succeeded = True
                if entry.event is not None:
                    entry.event.set()
            acknowledged_command = entry.command if entry is not None else self.last_command
            if acknowledged_command in {"L0", "L1"}:
                self._set_state(READY, f"Calibration acknowledged ({acknowledged_command})")
            elif acknowledged_command in {definition.start_command for definition in SENSORS.values()}:
                self._set_state(STREAM_REQUESTED, f"Stream acknowledged ({acknowledged_command}); waiting for samples")
            elif acknowledged_command in {definition.stop_command for definition in SENSORS.values()}:
                self._set_state(READY, "Sensor stream stopped")
        if reply.measurement is None:
            self._publish_event(BackendEvent(reply.kind, self.port, reply.message, reply))
            return

        now_monotonic = monotonic()
        measurement = reply.measurement
        with self._sample_condition:
            self._record_measurement_locked(measurement.sensor_id)
        volume = None
        if measurement.sensor_id == "liquid_flow":
            with self._flow_lock:
                volume = self._integrate_flow_sample_locked(measurement.value, now_monotonic)

        sample = SensorSample(
            timestamp=datetime.now(timezone.utc),
            elapsed_seconds=now_monotonic - self._opened_at,
            board_port=self.port,
            sensor_id=measurement.sensor_id,
            value=measurement.value,
            unit=measurement.unit,
            accumulated_volume_ul=volume,
            raw_line=line,
            raw_value_ml_min=measurement.raw_value_ml_min,
        )
        # Only the sensor this session actually initialized may report the board
        # as streaming. A measurement from any other sensor (a pressure board
        # answering while liquid flow was requested, a late line from a stopped
        # stream) is still published, but it must not fabricate a STREAMING
        # state for a sensor that never produced a stream.
        if measurement.sensor_id == self.active_sensor_id:
            self._set_state(STREAMING, f"{measurement.sensor_id} measurement received")
        self._publish_event(BackendEvent("measurement", self.port, reply=reply, sample=sample))

    def _integrate_flow_sample_locked(self, value: float, now_monotonic: float) -> float:
        """Trapezoidal integration; must be called under ``_flow_lock``.

        A receive gap longer than the stream's own recent sample rate justifies
        is an unknown interval: start a new segment instead of inventing volume
        across missing data. The cutoff adapts to the observed sample rate so
        legitimately slow streams (below the fixed 2 s baseline) are still
        integrated, while gaps that clearly exceed the usual cadence are not.
        """

        if self._last_flow_time is not None:
            delta_seconds = max(0.0, now_monotonic - self._last_flow_time)
            if self._recent_flow_intervals:
                typical = statistics.median(self._recent_flow_intervals)
                gap_limit = max(BASE_FLOW_GAP_SECONDS, FLOW_GAP_RATE_MULTIPLIER * typical)
            else:
                gap_limit = BASE_FLOW_GAP_SECONDS
            # Trapezoidal integration: (µL/min) * seconds / 60 = µL.
            if delta_seconds <= gap_limit:
                self._volume_ul += (
                    (self._last_flow_value + value) / 2.0
                    * delta_seconds
                    / 60.0
                )
            # Track the observed cadence even when this particular gap was
            # not integrated; the median adapts to the sensor's real sample
            # rate. Only obviously dead gaps (stalls) are excluded so they
            # cannot inflate the rate estimate and start inventing volume.
            if delta_seconds <= FLOW_STALL_RECORD_SECONDS:
                self._recent_flow_intervals.append(delta_seconds)
        # protocol.parse_reply() performed the single unit conversion
        # (firmware mL/min -> UI/CSV µL/min). Keep that value unchanged.
        self._last_flow_time = now_monotonic
        self._last_flow_value = value
        return self._volume_ul

    def _set_state(self, state: str, message: str = "") -> None:
        self.state = state
        self._publish_event(BackendEvent("state", self.port, message or state))

    def _reset_integration(self, keep_total: bool = False) -> None:
        with self._flow_lock:
            if not keep_total:
                self._volume_ul = 0.0
            self._last_flow_time = None
            self._last_flow_value = None
            self._recent_flow_intervals.clear()

    def close(self) -> bool:
        """Shut down the board and always finish the teardown.

        Returns whether the board acknowledged the global pump power-off. The
        connection is closed regardless: a failed POFF must not leave the flow
        stream, the reader thread, and the port running behind a "closing"
        state. The return value only reports the power-off fact.
        """

        if not self.connection:
            return True
        if not self.is_open and self.state == DISCONNECTED:
            # Already fully torn down (idempotent). This also lets the UI's
            # "retry disconnect" flow succeed on the second attempt after a
            # failed first close.
            return True
        pumps_stopped = False
        try:
            # Flip the command gate and emit POFF/DFOFF atomically with respect
            # to any in-flight transaction: a sender either finishes its current
            # step before the flip or finds the gate closed once it acquires the
            # lock. This ordering is what prevents a late P<n>ON from landing
            # after the global power-off. _internal_write stays True only for
            # this teardown's own writes and is cleared before the lock is
            # released, so a waiting sender is rejected, not admitted.
            with self._command_lock:
                self._accepting_commands = False
                self._internal_write = True
                self._initialization_cancel.set()
                with self._sample_condition:
                    self._sample_condition.notify_all()
                self._set_state(CLOSING, "Closing Multiboard connection")
                # Attempt the global pump shutdown twice before giving up on
                # the acknowledgement, then continue with the remaining teardown.
                pumps_stopped = self.send_and_wait_for_ack("POFF", timeout=1.0)
                if not pumps_stopped and self.is_open:
                    pumps_stopped = self.send_and_wait_for_ack("POFF", timeout=1.0)
                if not pumps_stopped:
                    self._publish_event(
                        BackendEvent(
                            "error",
                            self.port,
                            "POFF was not acknowledged; shutting down anyway",
                        )
                    )
                try:
                    self.stop_sensor()
                except Exception as exc:
                    self._publish_event(
                        BackendEvent("error", self.port, f"DFOFF failed: {exc}")
                    )
                # Clear the internal bypass while still holding the lock so a
                # sender that acquires it next is gated out.
                self._internal_write = False
        except Exception as exc:
            self._publish_event(BackendEvent("error", self.port, f"Stop failed: {exc}"))
            self._internal_write = False
        self._stop_event.set()
        initializer = self._initializer
        if initializer is not None and initializer is not threading.current_thread():
            initializer.join(timeout=2.0)
        if self._reader is not None:
            self._reader.join(timeout=1.0)
        try:
            if getattr(self.connection, "is_open", False):
                self.connection.close()
        finally:
            self._internal_write = False
            # The backlog belongs to the session that just ended: keeping it
            # would shift every acknowledgement of a reconnect by one.
            self._release_pending_ack_entries()
            self.state = DISCONNECTED
            self._publish_event(BackendEvent("disconnected", self.port))
        return pumps_stopped


def open_and_start_liquid_flow(
    port: str,
    boot_delay_seconds: float = 1.0,
) -> MultiboardConnection:
    """Open the board and start an unfiltered liquid-flow stream."""

    board = MultiboardConnection(port)
    board.open()
    sleep(max(0.0, boot_delay_seconds))
    board.initialize_liquid_flow(max_attempts=3, monitor_stream=False)
    return board
