"""Threaded, UI-independent Multiboard2 serial sensor backend."""

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
from backend.flow_filter import FlowSignalFilter


BAUD_RATE = 115_200
MAX_FLOW_INTEGRATION_GAP_SECONDS = 2.0
# Keep V14's median/deadband protection, but respond faster so short flow
# changes are not visibly flattened by an overly slow EMA.
FLOW_FILTER_EMA_TIME_CONSTANT_SECONDS = 0.35
FLOW_FILTER_ZERO_DEADBAND_UL_MIN = 75.0
FLOW_FILTER_CONFIRMATION_SAMPLES = 2

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


@dataclass(frozen=True)
class BackendEvent:
    kind: str
    board_port: str
    message: str = ""
    reply: ParsedReply | None = None
    sample: SensorSample | None = None


class MultiboardConnection:
    """Own one COM port and publish parsed events through a thread-safe queue."""

    def __init__(
        self,
        port: str,
        connection: Any | None = None,
        *,
        filter_enabled: bool = True,
    ) -> None:
        self.port = port
        self.connection = connection
        self.filter_enabled = bool(filter_enabled)
        self.events: queue.Queue[BackendEvent] = queue.Queue()
        self.active_sensor_id: str | None = None
        self.firmware = "Unknown"
        self.state = DISCONNECTED
        self.last_rx_line = ""
        self.last_command = ""
        self._opened_at = monotonic()
        self._last_flow_time: float | None = None
        self._last_flow_value: float | None = None
        self._flow_filter = FlowSignalFilter(
            ema_time_constant_seconds=FLOW_FILTER_EMA_TIME_CONSTANT_SECONDS,
            zero_deadband_ul_min=FLOW_FILTER_ZERO_DEADBAND_UL_MIN,
            confirmation_samples=FLOW_FILTER_CONFIRMATION_SAMPLES,
        )
        self._volume_ul = 0.0
        self._stop_event = threading.Event()
        self._write_lock = threading.Lock()
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
        self.events.put(BackendEvent("connected", self.port))

    def send(self, command: str) -> None:
        if not self.is_open:
            raise RuntimeError(f"{self.port} is not open")
        payload = encode_command(command)
        with self._write_lock:
            self.connection.write(payload)
            self.connection.flush()
        self.last_command = command.strip()
        self.events.put(BackendEvent("command", self.port, self.last_command))

    def request_firmware(self) -> None:
        self._set_state(HANDSHAKE, "Requesting Multiboard firmware")
        self.send("V")

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
        self._flow_filter.reset()

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
                self.events.put(BackendEvent("error", self.port, str(exc)))
        finally:
            if buffer and not self._stop_event.is_set():
                self.events.put(
                    BackendEvent("unknown", self.port, buffer.decode("utf-8", errors="replace"))
                )

    def _handle_line(self, line: str) -> None:
        self.last_rx_line = line
        # Keep the original line visible to the UI/diagnostic log.  Parsing is
        # deliberately separate, so an unfamiliar firmware response cannot
        # silently look like a dead serial connection.
        self.events.put(BackendEvent("raw", self.port, line))
        reply = parse_reply(line, self.active_sensor_id)
        if reply.kind == "firmware":
            self.firmware = reply.message
            self._set_state(READY, f"Firmware identified: {reply.message}")
        elif reply.kind == "error":
            self._set_state(ERROR, f"Board rejected command: {reply.message}")
        elif reply.kind == "ack":
            if self.last_command in {"L0", "L1"}:
                self._set_state(READY, f"Calibration acknowledged ({self.last_command})")
            elif self.last_command in {definition.start_command for definition in SENSORS.values()}:
                self._set_state(STREAM_REQUESTED, f"Stream acknowledged ({self.last_command}); waiting for samples")
            elif self.last_command in {definition.stop_command for definition in SENSORS.values()}:
                self._set_state(READY, "Sensor stream stopped")
        if reply.measurement is None:
            self.events.put(BackendEvent(reply.kind, self.port, reply.message, reply))
            return

        now_monotonic = monotonic()
        measurement = reply.measurement
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
                    filtered_value = self._processed_flow_value(
                        measurement.value, delta_seconds
                    )
                    self._volume_ul += (
                        (self._last_flow_value + filtered_value) / 2.0
                        * delta_seconds
                        / 60.0
                    )
                else:
                    self._flow_filter.reset()
                    filtered_value = self._processed_flow_value(measurement.value, None)
            else:
                filtered_value = self._processed_flow_value(measurement.value, None)
            self._last_flow_time = now_monotonic
            self._last_flow_value = filtered_value
            volume = self._volume_ul

            # The sample passed downstream is the exact same signed signal used
            # by integration and CSV logging. This keeps Bartels-style reverse
            # flow visible and prevents graph/CSV/volume divergence.
            measurement = measurement.__class__(
                measurement.sensor_id,
                filtered_value,
                measurement.unit,
                measurement.raw_line,
                measurement.raw_value_ml_min,
            )

        sample = SensorSample(
            timestamp=datetime.now(timezone.utc),
            elapsed_seconds=now_monotonic - self._opened_at,
            board_port=self.port,
            sensor_id=measurement.sensor_id,
            value=measurement.value,
            unit=measurement.unit,
            accumulated_volume_ul=volume,
            raw_line=line,
        )
        self._set_state(STREAMING, f"{measurement.sensor_id} measurement received")
        self.events.put(BackendEvent("measurement", self.port, reply=reply, sample=sample))

    def _set_state(self, state: str, message: str = "") -> None:
        self.state = state
        self.events.put(BackendEvent("state", self.port, message or state))

    def _processed_flow_value(self, raw_value: float, delta_seconds: float | None) -> float:
        """Return either the untouched normalized reading or the display signal.

        The raw mode is deliberately selected after protocol parsing, so it
        still uses the same unit conversion, validation, timestamps, graph,
        CSV, and integration paths as filtered mode.
        """
        if not self.filter_enabled:
            return float(raw_value)
        return self._flow_filter.update(raw_value, delta_seconds)

    def _reset_integration(self, keep_total: bool = False) -> None:
        if not keep_total:
            self._volume_ul = 0.0
        self._last_flow_time = None
        self._last_flow_value = None
        self._flow_filter.reset()

    def close(self) -> None:
        if not self.connection:
            return
        self._set_state(CLOSING, "Closing Multiboard connection")
        try:
            self.stop_sensor()
        except Exception as exc:
            self.events.put(BackendEvent("error", self.port, f"Stop failed: {exc}"))
        self._stop_event.set()
        if self._reader is not None:
            self._reader.join(timeout=1.0)
        try:
            if getattr(self.connection, "is_open", False):
                self.connection.close()
        finally:
            self.state = DISCONNECTED
            self.events.put(BackendEvent("disconnected", self.port))


def open_and_start_liquid_flow(
    port: str,
    boot_delay_seconds: float = 1.0,
    *,
    filter_enabled: bool = True,
) -> MultiboardConnection:
    """Convenience handshake used by the current liquid-flow UI MVP."""

    board = MultiboardConnection(port, filter_enabled=filter_enabled)
    board.open()
    sleep(max(0.0, boot_delay_seconds))
    board.request_firmware()
    # Bartels' UI gives the board time to answer V before changing the
    # calibration/measurement mode.  Keep these writes separate; sending
    # L0/DFON back-to-back is the intermittent startup failure seen in V10.
    sleep(1.0)
    board.start_sensor(
        "liquid_flow",
        Calibration.WATER,
        calibration_delay_seconds=0.5,
    )
    return board
