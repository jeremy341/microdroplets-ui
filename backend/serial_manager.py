"""Threaded, UI-independent Multiboard2 serial backend.

The connection object owns one UART reader thread, serializes writes, parses
replies, publishes thread-safe ``BackendEvent`` objects, and performs the sensor
initialization/streaming state machine.

See ``docs/DEVELOPER_GUIDE.md`` for command/reply framing and
``docs/DEVELOPER_GUIDE.md`` for the sensor data path.
"""

from __future__ import annotations

import queue
import threading
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
MAX_FLOW_INTEGRATION_GAP_SECONDS = 2.0
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


class MultiboardConnection:
    """Own one COM port and publish parsed events through a thread-safe queue."""

    def __init__(
        self,
        port: str,
        connection: Any | None = None,
    ) -> None:
        self.port = port
        self.connection = connection
        self.events: queue.Queue[BackendEvent] = queue.Queue()
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
        self._last_flow_time: float | None = None
        self._last_flow_value: float | None = None
        self._volume_ul = 0.0
        self._stop_event = threading.Event()
        self._write_lock = threading.Lock()
        # Keep complete acknowledged command transactions atomic. Pump and
        # sensor commands share one UART, so another write must not replace
        # ``last_command`` while a caller is waiting for its OK/FAIL reply.
        self._command_lock = threading.RLock()
        self._ack_lock = threading.Lock()
        self._pending_ack_command: str | None = None
        self._pending_ack_event: threading.Event | None = None
        self._pending_ack_succeeded = False
        self._firmware_event = threading.Event()
        self._sample_condition = threading.Condition()
        self._measurement_count = 0
        self._initialization_cancel = threading.Event()
        self._initializer: threading.Thread | None = None
        self._reader: threading.Thread | None = None
    @property
    def is_open(self) -> bool:
        return bool(self.connection is not None and getattr(self.connection, "is_open", False))

    @property
    def accumulated_volume_ul(self) -> float:
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

    def send(self, command: str) -> None:
        # Command syntax belongs in backend.protocol; transport/framing and
        # acknowledgement behavior are documented in docs/DEVELOPER_GUIDE.md.
        if not self.is_open:
            raise RuntimeError(f"{self.port} is not open")
        payload = encode_command(command)
        with self._command_lock:
            with self._write_lock:
                self.last_command = command.strip()
                self.connection.write(payload)
                self.connection.flush()
            self._publish_event(BackendEvent("command", self.port, self.last_command))

    def send_and_wait_for_ack(self, command: str, timeout: float = 1.0) -> bool:
        """Send one command and require the board's ``OK`` response."""

        clean = command.strip()
        with self._command_lock:
            event = threading.Event()
            with self._ack_lock:
                if self._pending_ack_event is not None:
                    raise RuntimeError("Another acknowledged command is already pending")
                self._pending_ack_command = clean
                self._pending_ack_event = event
                self._pending_ack_succeeded = False
            try:
                self.send(clean)
                if not event.wait(max(0.0, float(timeout))):
                    return False
                with self._ack_lock:
                    return self._pending_ack_succeeded
            finally:
                with self._ack_lock:
                    if self._pending_ack_event is event:
                        self._pending_ack_command = None
                        self._pending_ack_event = None
                        self._pending_ack_succeeded = False

    def send_sequence(
        self,
        commands,
        *,
        timeout: float = 1.0,
        rollback_command: str | None = None,
    ) -> CommandSequenceResult:
        """Send a complete acknowledged transaction without interleaving writes.

        Pump startup uses this boundary so another sensor/pump command cannot
        replace ``last_command`` between the individual ACKs.  A rollback is
        deliberately caller-supplied; pump code uses only the affected
        channel's OFF command rather than a global POFF.
        """

        sequence = tuple(str(command).strip() for command in commands if str(command).strip())
        if not sequence:
            return CommandSequenceResult(True, ())

        with self._command_lock:
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
        self._set_state(HANDSHAKE, "Requesting Multiboard firmware")
        self.send("V")

    def request_firmware_and_wait(self, timeout: float = 2.0) -> bool:
        """Request identification and wait for either Ready or version text."""

        self._firmware_event.clear()
        self.request_firmware()
        return self._firmware_event.wait(max(0.0, float(timeout)))

    def attach_initializer(self, thread: threading.Thread) -> None:
        self._initializer = thread

    def _wait_for_measurements(self, starting_count: int, required: int, timeout: float) -> bool:
        deadline = monotonic() + max(0.0, float(timeout))
        with self._sample_condition:
            while self._measurement_count - starting_count < required:
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

                self.active_sensor_id = "liquid_flow"
                self._reset_integration()
                with self._sample_condition:
                    starting_count = self._measurement_count
                self._set_state(STREAM_REQUESTED, "Requesting liquid-flow samples…")
                if not self.send_and_wait_for_ack(
                    SENSORS["liquid_flow"].start_command, timeout=1.5
                ):
                    raise RuntimeError("DFON was not acknowledged")
                if self._wait_for_measurements(
                    starting_count, required=2, timeout=sample_timeout_seconds
                ):
                    self._set_state(STREAMING, "Liquid-flow sensor connected")
                    if not monitor_stream:
                        return True
                    # Stay alive as a lightweight watchdog. If valid lines
                    # stop for five seconds, run the complete acknowledged
                    # DFOFF/L0/DFON sequence again instead of retrying DFON
                    # once and waiting forever.
                    with self._sample_condition:
                        observed_count = self._measurement_count
                    while self.is_open and not self._initialization_cancel.is_set():
                        with self._sample_condition:
                            self._sample_condition.wait(timeout=5.0)
                            current_count = self._measurement_count
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
        self._volume_ul = 0.0
        self._last_flow_time = None
        self._last_flow_value = None

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
        self.events.put(event)
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
        except BaseException as exc:
            if not self._stop_event.is_set():
                self._set_state(ERROR, f"Serial reader failed: {exc}")
                self._publish_event(BackendEvent("error", self.port, str(exc)))
        finally:
            if buffer and not self._stop_event.is_set():
                self._publish_event(
                    BackendEvent("unknown", self.port, buffer.decode("utf-8", errors="replace"))
                )

    def _handle_line(self, line: str) -> None:
        self.last_rx_line = line
        # Keep the original line visible to the UI/diagnostic log.  Parsing is
        # deliberately separate, so an unfamiliar firmware response cannot
        # silently look like a dead serial connection.
        self._publish_event(BackendEvent("raw", self.port, line))
        reply = parse_reply(line, self.active_sensor_id)
        if reply.kind == "firmware":
            self.firmware = reply.message
            self._firmware_event.set()
            self._set_state(READY, f"Firmware identified: {reply.message}")
        elif reply.kind == "boot":
            self._firmware_event.set()
        elif reply.kind == "error":
            with self._ack_lock:
                if self._pending_ack_event is not None:
                    self._pending_ack_succeeded = False
                    self._pending_ack_event.set()
            self._set_state(ERROR, f"Board rejected command: {reply.message}")
        elif reply.kind == "ack":
            with self._ack_lock:
                if (
                    self._pending_ack_event is not None
                    and self._pending_ack_command == self.last_command
                ):
                    self._pending_ack_succeeded = True
                    self._pending_ack_event.set()
            if self.last_command in {"L0", "L1"}:
                self._set_state(READY, f"Calibration acknowledged ({self.last_command})")
            elif self.last_command in {definition.start_command for definition in SENSORS.values()}:
                self._set_state(STREAM_REQUESTED, f"Stream acknowledged ({self.last_command}); waiting for samples")
            elif self.last_command in {definition.stop_command for definition in SENSORS.values()}:
                self._set_state(READY, "Sensor stream stopped")
        if reply.measurement is None:
            self._publish_event(BackendEvent(reply.kind, self.port, reply.message, reply))
            return

        now_monotonic = monotonic()
        measurement = reply.measurement
        with self._sample_condition:
            self._measurement_count += 1
            self._sample_condition.notify_all()
        volume = None
        if measurement.sensor_id == "liquid_flow":
            delta_seconds = None
            if self._last_flow_time is not None and self._last_flow_value is not None:
                delta_seconds = max(0.0, now_monotonic - self._last_flow_time)
                # Trapezoidal integration: (µL/min) * seconds / 60 = µL.
                # A long receive gap is an unknown interval, not evidence that
                # the previous flow continued. Start a new segment instead of
                # inventing volume across missing data.
                if delta_seconds <= MAX_FLOW_INTEGRATION_GAP_SECONDS:
                    self._volume_ul += (
                        (self._last_flow_value + measurement.value) / 2.0
                        * delta_seconds
                        / 60.0
                    )
            # protocol.parse_reply() performed the single unit conversion
            # (firmware mL/min -> UI/CSV µL/min). Keep that value unchanged.
            self._last_flow_time = now_monotonic
            self._last_flow_value = measurement.value
            volume = self._volume_ul

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
        self._set_state(STREAMING, f"{measurement.sensor_id} measurement received")
        self._publish_event(BackendEvent("measurement", self.port, reply=reply, sample=sample))

    def _set_state(self, state: str, message: str = "") -> None:
        self.state = state
        self._publish_event(BackendEvent("state", self.port, message or state))

    def _reset_integration(self, keep_total: bool = False) -> None:
        if not keep_total:
            self._volume_ul = 0.0
        self._last_flow_time = None
        self._last_flow_value = None

    def close(self) -> bool:
        if not self.connection:
            return True
        self._initialization_cancel.set()
        with self._sample_condition:
            self._sample_condition.notify_all()
        self._set_state(CLOSING, "Closing Multiboard connection")
        try:
            # Do not discard the connection until the board confirms the
            # global pump shutdown.  This prevents a failed write from being
            # reported as a safe disconnect.
            pumps_stopped = self.send_and_wait_for_ack("POFF", timeout=1.0)
            if not pumps_stopped:
                pumps_stopped = self.send_and_wait_for_ack("POFF", timeout=1.0)
            if not pumps_stopped:
                message = "POFF was not acknowledged; connection kept open"
                self._publish_event(BackendEvent("error", self.port, message))
                return False
            self.stop_sensor()
        except Exception as exc:
            self._publish_event(BackendEvent("error", self.port, f"Stop failed: {exc}"))
            return False
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
            self.state = DISCONNECTED
            self._publish_event(BackendEvent("disconnected", self.port))
        return True


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
