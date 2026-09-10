"""Non-blocking CSV logging in FluidicStudio's compact CSV format.

The logger consumes normalized samples on its own worker thread so sensor
streaming and the Qt event loop are not blocked by disk I/O.  See
``docs/DEVELOPER_GUIDE.md`` for the complete sensor/logging pipeline.
"""

from __future__ import annotations

import csv
import queue
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

CSV_HEADER = ("Timestamp",)


@dataclass(frozen=True)
class LogSample:
    timestamp: datetime
    board_id: str
    sensor_id: str
    sensor_type: str
    value: float
    precision: int
    unit: str
    accumulated_volume_ul: float | None
    raw_line: str = ""
    raw_value_ml_min: float | None = None


class AsyncCsvLogger:
    """Write received samples on a worker thread using Bartels' layout.

    FluidicStudio writes a short metadata preamble followed by a semicolon-
    separated table.  The first column is a sample number, not a wall-clock
    timestamp.  Raw serial text and the long-form audit fields remain in the
    diagnostic stream, not in this user-facing export.
    """

    def __init__(self, path: Path, interval_seconds: float, series_labels: tuple[str, ...] = ()) -> None:
        self.path = Path(path)
        self.raw_path = self.path.with_name(f"{self.path.stem}_raw{self.path.suffix}")
        self.interval_seconds = max(0.1, float(interval_seconds))
        self.series_labels = tuple(series_labels)
        self._queue: queue.Queue[LogSample | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._started_at: datetime | None = None
        self._status = "idle"
        self._error: str | None = None
        self._state_lock = threading.Lock()

    @property
    def status(self) -> str:
        with self._state_lock:
            return self._status

    @property
    def error(self) -> str | None:
        with self._state_lock:
            return self._error

    @property
    def is_accepting(self) -> bool:
        return self.status in {"starting", "running"}

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("CSV logger has already been started")
        with self._state_lock:
            self._status = "starting"
            self._error = None
        self._thread = threading.Thread(
            target=self._worker,
            name="sensor-csv-logger",
            daemon=True,
        )
        self._thread.start()

    def submit(self, sample: LogSample) -> bool:
        if not self.is_accepting:
            return False
        self._queue.put_nowait(sample)
        return True

    def stop(self, timeout: float = 2.0) -> None:
        thread = self._thread
        if thread is None:
            return
        if thread.is_alive():
            self._queue.put(None)
            thread.join(timeout=max(0.0, timeout))
        with self._state_lock:
            if self._status not in {"failed", "stopped"}:
                self._status = "stopped" if not thread.is_alive() else "stopping"

    def _set_failed(self, exc: BaseException) -> None:
        with self._state_lock:
            self._error = str(exc) or exc.__class__.__name__
            self._status = "failed"

    def _worker(self) -> None:
        # Keep one pending representative per series and interval. The old
        # implementation wrote the first sample and discarded the rest until
        # the interval elapsed, which could silently lose a short real peak.
        pending: dict[tuple[str, str], LogSample] = {}
        interval_started: dict[tuple[str, str], datetime] = {}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with (
                self.path.open("w", newline="", encoding="utf-8") as file,
                self.raw_path.open("w", newline="", encoding="utf-8") as raw_file,
            ):
                writer = csv.writer(file, delimiter=";", lineterminator="\n")
                raw_writer = csv.writer(raw_file, delimiter=";", lineterminator="\n")
                raw_writer.writerow(
                    (
                        "Timestamp",
                        "Board",
                        "Sensor",
                        "Raw line",
                        "Raw value (mL/min)",
                        "Normalized value (µL/min)",
                        "Accumulated volume (µL)",
                    )
                )
                start_time = datetime.now().astimezone().replace(microsecond=0)
                writer.writerow(("Logging Start Time:", start_time.strftime("%Y-%m-%d %H:%M:%S")))
                samples_per_second = 1.0 / self.interval_seconds
                rate_text = f"{samples_per_second:.6g}".replace(".", ",")
                writer.writerow(("Sample Rate:", rate_text, "samples/second"))
                writer.writerow(())
                labels = list(self.series_labels)
                if labels:
                    writer.writerow(("Timestamp", *labels))
                file.flush()
                with self._state_lock:
                    self._status = "running"

                sample_index = 0

                def write_sample(sample: LogSample) -> None:
                    nonlocal sample_index, labels
                    label = f"{sample.board_id} - {sample.sensor_type.replace(' ', '')}"
                    if label not in labels:
                        labels.append(label)
                    sample_index += 1
                    value_text = f"{sample.value:.{sample.precision}f}".replace(".", ",")
                    values = [""] * len(labels)
                    values[labels.index(label)] = value_text
                    writer.writerow((sample_index, *values))
                    file.flush()

                while True:
                    sample = self._queue.get()
                    if sample is None:
                        for pending_sample in sorted(
                            pending.values(), key=lambda item: item.timestamp
                        ):
                            write_sample(pending_sample)
                        break

                    # The sidecar is intentionally unsampled and unrounded:
                    # every parsed firmware value remains available for
                    # diagnostics, while the regular CSV keeps Bartels' layout.
                    raw_writer.writerow(
                        (
                            sample.timestamp.isoformat(timespec="milliseconds"),
                            sample.board_id,
                            sample.sensor_id,
                            sample.raw_line,
                            "" if sample.raw_value_ml_min is None else format(sample.raw_value_ml_min, ".15g"),
                            format(sample.value, ".15g"),
                            "" if sample.accumulated_volume_ul is None else format(sample.accumulated_volume_ul, ".15g"),
                        )
                    )
                    raw_file.flush()

                    series_id = (sample.board_id, sample.sensor_id)
                    current = pending.get(series_id)
                    if current is None:
                        pending[series_id] = sample
                        interval_started[series_id] = sample.timestamp
                        continue

                    elapsed = (sample.timestamp - interval_started[series_id]).total_seconds()
                    if elapsed < self.interval_seconds:
                        # Preserve the signed value with the greatest
                        # magnitude so positive and negative transient peaks
                        # both remain visible in the interval CSV.
                        if abs(sample.value) > abs(current.value):
                            pending[series_id] = sample
                        continue

                    write_sample(current)
                    pending[series_id] = sample
                    interval_started[series_id] = sample.timestamp
        except BaseException as exc:
            self._set_failed(exc)
            return

        with self._state_lock:
            self._status = "stopped"
