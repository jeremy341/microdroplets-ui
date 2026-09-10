"""Non-consuming sensor-event fan-out for Workspace and future compact views."""

from __future__ import annotations

from collections import deque
from datetime import datetime
import threading


class SensorDataHub:
    """Keep a small thread-safe history without draining Multiboard queues.

    ``SensorsPage`` remains the owner of the existing detailed UI/logging path.
    The hub subscribes to the same MultiboardConnection events so Workspace can
    render live data independently without stealing queue items from Sensors.
    """

    def __init__(self, history_seconds: float = 10 * 60):
        self.history_seconds = max(30.0, float(history_seconds))
        self._lock = threading.RLock()
        self._histories: dict[str, deque] = {}
        self._subscriptions: dict[object, object] = {}
        self._available: set[str] = set()
        self._latest: dict[str, float] = {}

    @staticmethod
    def _key(port: str, sensor_id: str) -> str:
        return f"{port}:{sensor_id}"

    def set_connected_boards(self, boards) -> None:
        desired = {}
        for board in boards or ():
            connection = board.get("connection") if isinstance(board, dict) else None
            if connection is not None:
                desired[connection] = str(board.get("port", getattr(connection, "port", "")))

        with self._lock:
            current_connections = set(self._subscriptions)

        for connection in current_connections - set(desired):
            self._unsubscribe(connection)
        for connection, port in desired.items():
            with self._lock:
                already = connection in self._subscriptions
            if not already:
                self._subscribe(connection, port)

    def _subscribe(self, connection, port: str) -> None:
        def consume(event, board_port=port):
            self._on_event(board_port, event)

        subscribe = getattr(connection, "subscribe_events", None)
        if not callable(subscribe):
            return
        subscribe(consume)
        with self._lock:
            self._subscriptions[connection] = consume

    def _unsubscribe(self, connection) -> None:
        with self._lock:
            callback = self._subscriptions.pop(connection, None)
        if callback is None:
            return
        unsubscribe = getattr(connection, "unsubscribe_events", None)
        if callable(unsubscribe):
            try:
                unsubscribe(callback)
            except Exception:
                pass

    def _on_event(self, board_port: str, event) -> None:
        sample = getattr(event, "sample", None)
        if sample is None:
            return
        sensor_id = str(getattr(sample, "sensor_id", ""))
        if not sensor_id:
            return
        key = self._key(board_port, sensor_id)
        timestamp = getattr(sample, "timestamp", None)
        if isinstance(timestamp, datetime):
            dt = timestamp.astimezone().replace(tzinfo=None)
            seconds = dt.timestamp()
        else:
            dt = datetime.now()
            seconds = dt.timestamp()
        value = float(getattr(sample, "value", 0.0))

        with self._lock:
            history = self._histories.setdefault(key, deque())
            history.append((seconds, dt, value))
            cutoff = seconds - self.history_seconds
            while history and history[0][0] < cutoff:
                history.popleft()
            self._available.add(key)
            self._latest[key] = value

    def snapshot(self, board_port: str, sensor_id: str):
        key = self._key(str(board_port), str(sensor_id))
        with self._lock:
            rows = tuple(self._histories.get(key, ()))
        return (
            [row[2] for row in rows],
            [row[1] for row in rows],
        )

    def latest(self, board_port: str, sensor_id: str):
        key = self._key(str(board_port), str(sensor_id))
        with self._lock:
            return self._latest.get(key)

    def is_available(self, board_port: str, sensor_id: str) -> bool:
        key = self._key(str(board_port), str(sensor_id))
        with self._lock:
            return key in self._available

    def clear(self) -> None:
        with self._lock:
            self._histories.clear()
            self._available.clear()
            self._latest.clear()

    def shutdown(self) -> None:
        with self._lock:
            connections = tuple(self._subscriptions)
        for connection in connections:
            self._unsubscribe(connection)
