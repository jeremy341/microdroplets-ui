"""Raw liquid-flow capture helpers for the Bartels mp-Multiboard2 / SLF3S.

These helpers were migrated from the one-off ``bartels_flow_test_v1.py`` CLI
script.  They preserve the board's raw ``V`` / ``RSLF`` sensor values in
mL/min without any filtering, and record every TX/RX line to a CSV so the
unfiltered data stays available for parser development.  The serial transport
itself lives in :mod:`backend.diagnostics`.
"""

from __future__ import annotations

import csv
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic

NUMBER = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
VALUE_RE = re.compile(rf"^V\s*=\s*({NUMBER})\s*$", re.IGNORECASE)
MARKED_RE = re.compile(rf"^(?:RSLF|RSLF\s*=)\s*({NUMBER})\s*$", re.IGNORECASE)


def documents_dir() -> Path:
    """Return Windows Documents, with a safe cross-platform fallback."""

    # The real target is Windows.  In Linux CI/sandbox environments the
    # runtime home can be read-only, so use a project-local fallback there.
    if os.name != "nt":
        fallback = Path.cwd() / "Documents"
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback

    home = Path.home()
    candidates = [home / "Documents", home / "My Documents"]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    # On first use Windows normally creates Documents already; creating it is
    # harmless and makes the output location deterministic in test VMs too.
    candidates[0].mkdir(parents=True, exist_ok=True)
    return candidates[0]


def unique_csv_path(directory: Path, prefix: str = "bartels_flow_test") -> Path:
    """Create a never-overwriting filename, even for repeated same-second runs."""

    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    base = directory / f"{prefix}_{stamp}.csv"
    candidate = base
    counter = 1
    while candidate.exists():
        candidate = directory / f"{prefix}_{stamp}_{counter:02d}.csv"
        counter += 1
    return candidate


def parse_liquid_flow(line: str) -> float | None:
    """Return the board's liquid-flow value in ml/min, without altering it."""

    clean = line.strip()
    match = VALUE_RE.fullmatch(clean)
    if match:
        return float(match.group(1))
    match = MARKED_RE.fullmatch(clean)
    return float(match.group(1)) if match else None


def is_flow_input(value_ml_min: float, threshold_ml_min: float) -> bool:
    """Use a raw-value threshold only to detect the beginning of a push."""

    return abs(value_ml_min) >= threshold_ml_min


def measurement_schedule(baseline: float, flow_window: float, post: float) -> tuple[str, ...]:
    """Return the automatic measurement phases in their required order."""

    if baseline < 0 or flow_window <= 0 or post < 0:
        raise ValueError("baseline/post must be >= 0 and flow-window must be > 0")
    return ("baseline", "armed", "flow", "post")


class CsvCapture:
    """Flush-per-row CSV capture of the raw flow transcript."""

    HEADER = (
        "timestamp_utc", "elapsed_seconds", "phase", "direction", "payload",
        "payload_hex", "flow_ml_min", "flow_ul_min", "interval_seconds",
        "integrated_volume_ul",
    )

    def __init__(self, path: Path) -> None:
        self.path = path
        self.started = monotonic()
        path.parent.mkdir(parents=True, exist_ok=True)
        self.file = path.open("x", newline="", encoding="utf-8")
        self.writer = csv.writer(self.file)
        self.writer.writerow(self.HEADER)
        self.file.flush()

    def write(self, direction: str, raw: bytes, phase: str,
              flow_ml_min: float | None = None,
              interval_seconds: float | None = None,
              integrated_volume_ul: float | None = None) -> None:
        elapsed = monotonic() - self.started
        self.writer.writerow((
            datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            f"{elapsed:.6f}", phase, direction,
            raw.decode("utf-8", errors="backslashreplace").rstrip("\r\n"),
            raw.hex(" "),
            "" if flow_ml_min is None else f"{flow_ml_min:.9f}",
            "" if flow_ml_min is None else f"{flow_ml_min * 1000.0:.6f}",
            "" if interval_seconds is None else f"{interval_seconds:.6f}",
            "" if integrated_volume_ul is None else f"{integrated_volume_ul:.6f}",
        ))
        self.file.flush()

    def close(self) -> None:
        if not self.file.closed:
            self.file.flush()
            self.file.close()
