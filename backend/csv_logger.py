"""Non-blocking CSV logging in FluidicStudio's compact CSV format.

The logger consumes normalized samples on its own worker thread so sensor
streaming and the Qt event loop are not blocked by disk I/O.  See
``docs/DEVELOPER_GUIDE.md`` for the complete sensor/logging pipeline.

The column header is written lazily from the series actually received, so a
logging session that starts before sensor detection still produces a labelled
CSV. Series discovered mid-session rewrite the header row once so the new
columns are labelled too.
"""

from __future__ import annotations

import csv
import queue
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

CSV_HEADER = ("Timestamp",)
# Bounds the internal queue so a stalled writer thread cannot grow memory
# without limit. Overflow drops the oldest queued sample and is counted.
QUEUE_LIMIT = 50_000
# A forward timestamp step larger than this is a clock jump, not ordinary
# sampling: the series is re-anchored on the new sample instead of resuming an
# interval grid that no longer describes the data. Comfortably longer than any
# real gap in a logging session.
MAX_INTERVAL_SECONDS = 86_400.0
# A backward step this small is measurement noise, not a clock step: real
# timestamps jitter by microseconds (host clock granularity, float rounding
# when epoch seconds are converted to a datetime), and every such step must keep
# folding into the pending interval row instead of emitting a row of its own.
# 1 ms is a hundredth of the shortest interval the logger accepts (0.1 s) and
# nearly five orders of magnitude below the tens-of-seconds backward steps that
# really do have to break a segment, so the dead-band cannot mask a genuine
# clock step.
TIMESTAMP_BACKWARD_TOLERANCE_SECONDS = 1e-3


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
        self._queue: queue.Queue[LogSample | None] = queue.Queue(maxsize=QUEUE_LIMIT)
        self._thread: threading.Thread | None = None
        self._started_at: datetime | None = None
        self._status = "idle"
        self._error: str | None = None
        self._dropped = 0
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
    def dropped_samples(self) -> int:
        """Number of samples discarded because the write queue was full."""

        with self._state_lock:
            return self._dropped

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
        # The status check and the enqueue must be atomic under the same lock
        # stop() uses to flip the status: a sample either lands in the queue
        # before the stop sentinel (and is therefore drained and written), or
        # it is rejected. There is no accepted-but-lost window.
        with self._state_lock:
            if self._status not in {"starting", "running"}:
                return False
            try:
                self._queue.put_nowait(sample)
            except queue.Full:
                # Bounded-queue policy: shed the oldest pending sample rather
                # than growing memory without limit; the loss is counted.
                try:
                    self._queue.get_nowait()
                    self._dropped += 1
                except queue.Empty:
                    pass
                try:
                    self._queue.put_nowait(sample)
                except queue.Full:
                    self._dropped += 1
                    return False
        return True

    def stop(self, timeout: float = 2.0) -> None:
        thread = self._thread
        if thread is None:
            return
        # Stop accepting new submissions before the sentinel so every sample
        # accepted before stop() is still drained and written by the worker.
        with self._state_lock:
            if self._status in {"starting", "running"}:
                self._status = "stopping"
        # The sentinel must be enqueued without blocking: a worker that died
        # mid-session leaves a full queue with no consumer, and a blocking
        # put here would hang the caller (and the app's shutdown path).
        while True:
            try:
                self._queue.put_nowait(None)
                break
            except queue.Full:
                if not thread.is_alive():
                    break
                try:
                    discarded = self._queue.get_nowait()
                except queue.Empty:
                    break
                # The queue was full, so a sample that submit() already
                # accepted has to be shed to make room for the sentinel.
                # Count it: otherwise dropped_samples understates the real
                # loss and the gap is invisible in every diagnostic.
                if discarded is not None:
                    with self._state_lock:
                        self._dropped += 1
        if thread.is_alive():
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
                file.flush()
                with self._state_lock:
                    # stop() may already have flipped the status while this
                    # worker was still starting up. Restoring "running" then
                    # would reopen is_accepting after the drain loop has
                    # stopped reading, stranding an accepted sample.
                    if self._status == "starting":
                        self._status = "running"
                labels = list(self.series_labels)
                # Columns are assigned per (board_id, sensor_id) - the same key
                # the interval aggregation uses - so two distinct sensors can
                # never write into one unattributable column.
                label_by_series: dict[tuple[str, str], str] = {}
                series_by_label: dict[str, tuple[str, str]] = {}
                header_written = bool(labels)
                if header_written:
                    writer.writerow(("Timestamp", *labels))

                sample_index = 0

                def series_label(sample: LogSample) -> str:
                    series_id = (sample.board_id, sample.sensor_id)
                    label = label_by_series.get(series_id)
                    if label is not None:
                        return label
                    label = f"{sample.board_id} - {sample.sensor_type.replace(' ', '')}"
                    owner = series_by_label.get(label)
                    if owner is not None and owner != series_id:
                        # A second sensor of the same type on the same board
                        # would collide with the readable label. Keep that
                        # label for the sensor that claimed it first and
                        # qualify the newcomer with its sensor id.
                        base = f"{label} [{sample.sensor_id}]"
                        label = base
                        duplicate = 2
                        while series_by_label.get(label, series_id) != series_id:
                            label = f"{base} #{duplicate}"
                            duplicate += 1
                    if label not in labels:
                        labels.append(label)
                        if header_written:
                            # A series discovered mid-session: rewrite the
                            # header so the new column is labelled too.
                            writer.writerow(("Timestamp", *labels))
                    label_by_series[series_id] = label
                    series_by_label[label] = series_id
                    return label

                def write_sample(sample: LogSample) -> None:
                    nonlocal sample_index
                    label = series_label(sample)
                    sample_index += 1
                    value_text = f"{sample.value:.{sample.precision}f}".replace(".", ",")
                    values = [""] * len(labels)
                    values[labels.index(label)] = value_text
                    writer.writerow((sample_index, *values))
                    file.flush()

                stop_requested = False
                while True:
                    if stop_requested:
                        # Drain samples that slipped past the is_accepting
                        # guard (submit/stop race) so "accepted" always means
                        # "written".
                        try:
                            sample = self._queue.get_nowait()
                        except queue.Empty:
                            break
                    else:
                        sample = self._queue.get()
                    if sample is None:
                        stop_requested = True
                        continue

                    # Register the sample's series before writing the lazy
                    # header so the first header already contains the column.
                    series_label(sample)
                    if not header_written:
                        header_written = True
                        writer.writerow(("Timestamp", *labels))
                        file.flush()

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
                    if elapsed < -TIMESTAMP_BACKWARD_TOLERANCE_SECONDS or elapsed > MAX_INTERVAL_SECONDS:
                        # The sample is not on the interval grid this series is
                        # already on: a genuinely backward clock step (host NTP
                        # step, device reset, replayed batch - all far larger
                        # than TIMESTAMP_BACKWARD_TOLERANCE_SECONDS) or an
                        # implausibly large forward jump. Folding it into
                        # `pending` would keep every following sample in the
                        # same slot until the clock caught up, silently
                        # collapsing a whole burst into a single row. Close the
                        # current interval and anchor a new one on this sample
                        # instead.
                        write_sample(current)
                        pending[series_id] = sample
                        interval_started[series_id] = sample.timestamp
                        continue

                    if elapsed < self.interval_seconds:
                        # Shorter than the interval - including a backward step
                        # inside the jitter dead-band, which is treated as part
                        # of the pending slot and must not move the anchor off
                        # the interval grid. Preserve the signed value with the
                        # greatest magnitude so positive and negative transient
                        # peaks both remain visible in the interval CSV.
                        if abs(sample.value) > abs(current.value):
                            pending[series_id] = sample
                        continue

                    write_sample(current)
                    pending[series_id] = sample
                    interval_started[series_id] = sample.timestamp

                for pending_sample in sorted(
                    pending.values(), key=lambda item: item.timestamp
                ):
                    write_sample(pending_sample)
        except BaseException as exc:
            self._set_failed(exc)
            return

        with self._state_lock:
            self._status = "stopped"
