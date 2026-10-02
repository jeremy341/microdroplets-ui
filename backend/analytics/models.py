"""Typed data models for offline droplet video analysis.

The analytics backend deliberately has no Qt dependency.  UI code can consume
these dataclasses, while tests and future CLI tools can exercise the same
analysis pipeline without constructing the desktop application.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


ANALYZER_VERSION = "3.4"


@dataclass(frozen=True)
class VideoMetadata:
    path: Path
    width: int
    height: int
    fps: float
    frame_count: int
    duration_s: float

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["path"] = str(self.path)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "VideoMetadata":
        payload = dict(data)
        payload["path"] = Path(payload["path"])
        return cls(**payload)


@dataclass
class Calibration:
    """Pixel-to-physical scale associated with one optical configuration."""

    um_per_px: float | None = None
    resolution: tuple[int, int] | None = None
    source: str = "pixels"

    @property
    def calibrated(self) -> bool:
        return self.um_per_px is not None and self.um_per_px > 0

    def compatible_with(self, width: int, height: int) -> bool:
        return self.resolution is None or self.resolution == (width, height)

    def length_um(self, px: float | None) -> float | None:
        if px is None or not self.calibrated:
            return None
        return float(px) * float(self.um_per_px)

    def velocity_mm_s(self, px_s: float | None) -> float | None:
        value_um_s = self.length_um(px_s)
        return None if value_um_s is None else value_um_s / 1000.0


@dataclass
class AnalysisConfig:
    """Configuration stored independently from the source recording.

    ROI is normalized to the image dimensions so the same setup can be reused
    for recordings with an identical field of view.  Flow direction is stored
    in image-pixel coordinates as a normalized vector.
    """

    mode: str = "auto"
    roi: tuple[float, float, float, float] = (0.0, 0.0, 1.0, 1.0)
    flow_direction: tuple[float, float] | None = None
    calibration: Calibration = field(default_factory=Calibration)
    min_area_px: float | None = None
    max_area_px: float | None = None
    background_samples: int = 31

    def normalized_roi(self) -> tuple[float, float, float, float]:
        x, y, w, h = self.roi
        x = max(0.0, min(1.0, float(x)))
        y = max(0.0, min(1.0, float(y)))
        w = max(0.01, min(1.0 - x, float(w)))
        h = max(0.01, min(1.0 - y, float(h)))
        return x, y, w, h

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["roi"] = list(self.roi)
        data["flow_direction"] = list(self.flow_direction) if self.flow_direction else None
        if self.calibration.resolution is not None:
            data["calibration"]["resolution"] = list(self.calibration.resolution)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AnalysisConfig":
        calibration_data = dict(data.get("calibration") or {})
        resolution = calibration_data.get("resolution")
        if resolution is not None:
            calibration_data["resolution"] = tuple(int(v) for v in resolution)
        calibration = Calibration(**calibration_data)
        flow = data.get("flow_direction")
        return cls(
            mode=str(data.get("mode", "auto")),
            roi=tuple(float(v) for v in data.get("roi", (0, 0, 1, 1))),
            flow_direction=None if flow is None else tuple(float(v) for v in flow),
            calibration=calibration,
            min_area_px=data.get("min_area_px"),
            max_area_px=data.get("max_area_px"),
            background_samples=int(data.get("background_samples", 31)),
        )


@dataclass
class Detection:
    contour: Any
    centroid: tuple[float, float]
    area_px2: float
    bbox: tuple[int, int, int, int]
    touches_roi_edge: bool = False
    major_axis: tuple[float, float] | None = None
    length_px: float | None = None
    width_px: float | None = None
    min_s: float | None = None
    max_s: float | None = None
    min_t: float | None = None
    max_t: float | None = None
    line_a_intersects: bool = False
    refined_geometry: bool = False
    refinement_score: float | None = None
    artifact_overlap: float = 0.0
    edge_support: float = 0.0


@dataclass
class OverlayDetection:
    droplet_id: int
    contour: list[tuple[int, int]]
    centroid: tuple[float, float]
    length_start: tuple[float, float] | None = None
    length_end: tuple[float, float] | None = None
    valid_candidate: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "droplet_id": self.droplet_id,
            "contour": [list(point) for point in self.contour],
            "centroid": list(self.centroid),
            "length_start": None if self.length_start is None else list(self.length_start),
            "length_end": None if self.length_end is None else list(self.length_end),
            "valid_candidate": self.valid_candidate,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "OverlayDetection":
        def point(value):
            return None if value is None else tuple(float(v) for v in value)

        return cls(
            droplet_id=int(data["droplet_id"]),
            contour=[tuple(int(v) for v in p) for p in data.get("contour", [])],
            centroid=tuple(float(v) for v in data["centroid"]),
            length_start=point(data.get("length_start")),
            length_end=point(data.get("length_end")),
            valid_candidate=bool(data.get("valid_candidate", True)),
        )


@dataclass
class DropletMeasurement:
    droplet_id: int
    timestamp_s: float
    frame_index: int
    length_px: float | None
    width_px: float | None
    area_px2: float | None
    aspect_ratio: float | None
    velocity_px_s: float | None
    spacing_px: float | None
    method: str
    valid: bool
    rejection_reason: str = ""
    counted: bool = True
    length_um: float | None = None
    width_um: float | None = None
    velocity_mm_s: float | None = None
    spacing_um: float | None = None
    confidence_score: float | None = None
    observation_count: int = 0
    geometry_variation: float | None = None

    def apply_calibration(self, calibration: Calibration) -> None:
        self.length_um = calibration.length_um(self.length_px)
        self.width_um = calibration.length_um(self.width_px)
        self.velocity_mm_s = calibration.velocity_mm_s(self.velocity_px_s)
        self.spacing_um = calibration.length_um(self.spacing_px)


@dataclass
class AnalysisSummary:
    total_droplets: int = 0
    valid_droplets: int = 0
    mean_length_px: float | None = None
    mean_length_um: float | None = None
    generation_rate_s: float | None = None
    generation_period_s: float | None = None
    generation_period_mad_s: float | None = None
    temporal_event_count: int = 0
    count_confidence: float | None = None


@dataclass
class AnalysisResult:
    metadata: VideoMetadata
    config: AnalysisConfig
    # ``None`` means the recording offered no flow evidence at all.  The run
    # still projects onto an internal axis to stay computable, but an unknown
    # direction must never reach the persisted result as a measured vector, so
    # it is serialized as ``null`` exactly like the optional configuration value.
    flow_direction: tuple[float, float] | None
    roi_px: tuple[int, int, int, int]
    line_a_s: float
    line_b_s: float
    measurements: list[DropletMeasurement]
    summary: AnalysisSummary
    count_line_s: float | None = None
    overlays: dict[int, list[OverlayDetection]] = field(default_factory=dict)
    complete: bool = True
    processed_frames: int = 0
    skipped_frames: int = 0
    analyzer_version: str = ANALYZER_VERSION
    cache_key: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "metadata": self.metadata.to_dict(),
            "config": self.config.to_dict(),
            "flow_direction": list(self.flow_direction) if self.flow_direction else None,
            "roi_px": list(self.roi_px),
            "line_a_s": self.line_a_s,
            "line_b_s": self.line_b_s,
            "count_line_s": self.count_line_s,
            "measurements": [asdict(item) for item in self.measurements],
            "summary": asdict(self.summary),
            "overlays": {
                str(frame): [overlay.to_dict() for overlay in items]
                for frame, items in self.overlays.items()
            },
            "complete": self.complete,
            "processed_frames": self.processed_frames,
            "skipped_frames": self.skipped_frames,
            "analyzer_version": self.analyzer_version,
            "cache_key": self.cache_key,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AnalysisResult":
        flow = data.get("flow_direction")
        return cls(
            metadata=VideoMetadata.from_dict(data["metadata"]),
            config=AnalysisConfig.from_dict(data["config"]),
            flow_direction=None if flow is None else tuple(float(v) for v in flow),
            roi_px=tuple(int(v) for v in data["roi_px"]),
            line_a_s=float(data["line_a_s"]),
            line_b_s=float(data["line_b_s"]),
            measurements=[DropletMeasurement(**item) for item in data.get("measurements", [])],
            summary=AnalysisSummary(**data.get("summary", {})),
            count_line_s=(None if data.get("count_line_s") is None else float(data.get("count_line_s"))),
            overlays={
                int(frame): [OverlayDetection.from_dict(item) for item in items]
                for frame, items in (data.get("overlays") or {}).items()
            },
            complete=bool(data.get("complete", True)),
            processed_frames=int(data.get("processed_frames", 0)),
            skipped_frames=int(data.get("skipped_frames", 0)),
            analyzer_version=str(data.get("analyzer_version", ANALYZER_VERSION)),
            cache_key=str(data.get("cache_key", "")),
        )
