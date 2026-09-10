"""Human-readable CSV export for per-droplet Analytics measurements.

The complete machine-readable analysis configuration and metadata already live
in ``analysis.json``.  CSV is meant for scientists opening a result in Excel,
LibreOffice or pandas, so V66 keeps one row per droplet and avoids repeating
video/configuration metadata in every row.
"""

from __future__ import annotations

import csv
from pathlib import Path

from .models import AnalysisResult


CSV_FIELDS = (
    "Droplet ID",
    "Time (s)",
    "Frame",
    "Length (px)",
    "Length (µm)",
    "Width (px)",
    "Width (µm)",
    "Area (px²)",
    "Aspect Ratio",
    "Velocity (px/s)",
    "Velocity (mm/s)",
    "Spacing (px)",
    "Spacing (µm)",
    "Confidence (%)",
    "Observations",
    "Geometry Variation",
    "Method",
    "Status",
    "Rejection Reason",
)


def unique_path(directory: Path, stem: str, suffix: str = ".csv") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    candidate = directory / f"{stem}{suffix}"
    if not candidate.exists():
        return candidate
    index = 2
    while True:
        candidate = directory / f"{stem}_{index:03d}{suffix}"
        if not candidate.exists():
            return candidate
        index += 1


def export_result_csv(result: AnalysisResult, destination: str | Path) -> Path:
    """Export one compact, readable row per tracked droplet.

    Physical-unit columns remain blank when no valid optical calibration is
    available.  This is deliberate: a camera's pixels-to-micrometre scale is
    specific to the microscope magnification and working geometry.
    """

    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for item in result.measurements:
            writer.writerow(
                {
                    "Droplet ID": item.droplet_id,
                    "Time (s)": _fmt(item.timestamp_s, 3),
                    "Frame": item.frame_index,
                    "Length (px)": _fmt(item.length_px, 1),
                    "Length (µm)": _fmt(item.length_um, 1),
                    "Width (px)": _fmt(item.width_px, 1),
                    "Width (µm)": _fmt(item.width_um, 1),
                    "Area (px²)": _fmt(item.area_px2, 1),
                    "Aspect Ratio": _fmt(item.aspect_ratio, 2),
                    "Velocity (px/s)": _fmt(item.velocity_px_s, 1),
                    "Velocity (mm/s)": _fmt(item.velocity_mm_s, 3),
                    "Spacing (px)": _fmt(item.spacing_px, 1),
                    "Spacing (µm)": _fmt(item.spacing_um, 1),
                    "Confidence (%)": _fmt(None if item.confidence_score is None else item.confidence_score * 100.0, 1),
                    "Observations": item.observation_count,
                    "Geometry Variation": _fmt(item.geometry_variation, 3),
                    "Method": str(item.method or "").replace("_", " ").title(),
                    "Status": "Valid" if item.valid else "Rejected",
                    "Rejection Reason": item.rejection_reason or "",
                }
            )
    return target


def _fmt(value, decimals: int) -> str:
    if value is None:
        return ""
    return f"{float(value):.{decimals}f}"
