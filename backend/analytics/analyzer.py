"""End-to-end offline droplet video analysis orchestration."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

from .detector import DropletDetector, apply_flow_geometry
from .models import (
    ANALYZER_VERSION,
    AnalysisConfig,
    AnalysisResult,
    AnalysisSummary,
    DropletMeasurement,
    OverlayDetection,
)
from .tracker import (
    DropletTracker,
    overlay_for_detection,
    recompute_track_events,
    suppress_duplicate_tracks,
    stitch_tracks,
    track_confidence,
    track_to_measurement,
)
from .temporal import crossing_events, match_event_times
from .video_source import VideoSource


ProgressCallback = Callable[[int, int], None]
PreviewCallback = Callable[[int, np.ndarray, list[OverlayDetection]], None]
CancelCheck = Callable[[], bool]


def _unit(vector: tuple[float, float]) -> tuple[float, float]:
    x, y = float(vector[0]), float(vector[1])
    norm = math.hypot(x, y)
    if norm <= 1e-9:
        return 1.0, 0.0
    return x / norm, y / norm


def _projection_bounds(roi_px: tuple[int, int, int, int], flow: tuple[float, float]) -> tuple[float, float]:
    x, y, w, h = roi_px
    corners = ((x, y), (x + w, y), (x, y + h), (x + w, y + h))
    values = [px * flow[0] + py * flow[1] for px, py in corners]
    return min(values), max(values)


def compute_cache_key(path: Path, config: AnalysisConfig) -> str:
    stat = path.stat()
    payload = {
        "path": str(path.resolve()),
        "size": int(stat.st_size),
        "mtime_ns": int(stat.st_mtime_ns),
        "config": config.to_dict(),
        "analyzer_version": ANALYZER_VERSION,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


class DropletAnalyzer:
    """Offline analyzer with no GUI dependency."""

    def __init__(self, config: AnalysisConfig | None = None) -> None:
        self.config = config or AnalysisConfig()

    def analyze(
        self,
        path: str | Path,
        *,
        progress_callback: ProgressCallback | None = None,
        preview_callback: PreviewCallback | None = None,
        cancel_check: CancelCheck | None = None,
    ) -> AnalysisResult:
        source = VideoSource(path)
        config = self._config_for_video(source)
        # Cache identity follows the user's requested configuration.  Auto mode
        # may derive a narrower effective ROI below, but the same source + user
        # settings must still resolve to the same cached analysis.
        cache_config = AnalysisConfig.from_dict(config.to_dict())
        detector = DropletDetector.from_video(source, config)
        flow = _unit(config.flow_direction or self._estimate_flow_direction(source, detector))

        auto_roi = self._auto_channel_roi(source, detector, flow, config)
        if auto_roi is not None:
            config.roi = auto_roi
            detector = DropletDetector.from_video(source, config)
            if config.flow_direction is None:
                # Re-estimate after removing full-frame microscope artefacts.
                # This corrected a supplied 30-fps recording where a vertical
                # obstruction previously dominated the motion estimator even
                # though the real microfluidic channel was horizontal.
                flow = _unit(self._estimate_flow_direction(source, detector))

        roi_px = detector.context.roi_px
        min_s, max_s = _projection_bounds(roi_px, flow)
        span = max(1.0, max_s - min_s)
        # Provisional lines are only needed while tracking.  V66 chooses the
        # final measurement zone after seeing where complete tracks are visible.
        line_a_s = min_s + span * 0.40
        line_b_s = min_s + span * 0.60
        count_line_s = min_s + span * 0.50
        max_distance = max(20.0, min(90.0, span * 0.12))
        # Keep enough temporal samples for short/closely spaced droplets while
        # avoiding redundant CV work on high-FPS recordings.  30-fps material
        # is analysed at 15 Hz, 20-fps material at 10 Hz, and 10-fps material
        # keeps every frame.
        analysis_stride = max(1, int(round(source.metadata.fps / 12.0)))
        analysis_rate = source.metadata.fps / analysis_stride
        tracker = DropletTracker(
            flow,
            line_a_s,
            line_b_s,
            count_line_s,
            source.metadata.fps,
            max_distance,
            max_missed_frames=max(3, int(round(analysis_rate * 0.15))),
        )

        overlays: dict[int, list[OverlayDetection]] = {}
        processed_frames = 0
        complete = True
        preview_every = max(1, int(round(source.metadata.fps / 5.0)))
        total = source.metadata.frame_count

        for frame_index, frame in source.iter_frames():
            if cancel_check is not None and cancel_check():
                complete = False
                break
            # Always decode sequentially (fast and codec-safe), but only run
            # detector/refinement at the target analysis rate.
            if frame_index % analysis_stride != 0 and frame_index + 1 != total:
                processed_frames = frame_index + 1
                if progress_callback is not None and frame_index % preview_every == 0:
                    progress_callback(processed_frames, total)
                continue
            detections = detector.detect(frame)
            for detection in detections:
                # First project the fast motion contour so the local refiner
                # knows its coarse longitudinal/transverse size, then replace
                # it with the original-frame colour envelope when possible.
                apply_flow_geometry(detection, flow, line_a_s)
                detector.refine_detection(frame, detection, flow)
                apply_flow_geometry(detection, flow, line_a_s)
            detections = self._deduplicate_detections(detections)
            assignments = tracker.update(frame_index, detections)
            frame_overlays = [overlay_for_detection(track_id, detection, flow) for track_id, detection in assignments]
            if frame_overlays:
                overlays[frame_index] = frame_overlays
            processed_frames = frame_index + 1
            if progress_callback is not None and (frame_index % preview_every == 0 or frame_index + 1 == total):
                progress_callback(processed_frames, total)
            if preview_callback is not None and frame_index % preview_every == 0:
                preview_callback(frame_index, frame, frame_overlays)

        tracks = tracker.finalize()

        # Remove simultaneous inner-reflection tracks before stitching temporal
        # fragments.  This is the track-level counterpart to frame NMS.
        tracks, duplicate_map = suppress_duplicate_tracks(tracks, flow)
        overlays = self._remap_overlays(overlays, duplicate_map)

        # Offline analysis has information the live tracker cannot use: it can
        # see both sides of a short missed-detection gap.  Stitch only fragments
        # that continue the same forward trajectory and have compatible shape.
        tracks, stitch_map = stitch_tracks(tracks, flow, source.metadata.fps)
        overlays = self._remap_overlays(overlays, stitch_map)

        line_a_s, line_b_s, count_line_s = self._choose_analysis_lines(
            tracks,
            roi_px,
            flow,
            source.metadata.fps,
            detector.context.artifact_mask,
        )
        for track in tracks:
            recompute_track_events(track, flow, line_a_s, line_b_s, count_line_s)

        typical_length = self._typical_track_length(tracks, flow, source.metadata.fps)
        stable_length_reference = self._stable_recording_length_reference(
            tracks, flow, source.metadata.fps
        )

        # Independent thin-line temporal signal (kymograph-style second opinion).
        # It does not invent droplets; it can only recover a crossing timestamp
        # for a nearby existing track that was fragmented at the final count line.
        temporal = crossing_events(
            source,
            roi_px=roi_px,
            background_gray=detector.context.background_gray,
            artifact_mask=detector.context.artifact_mask,
            flow=flow,
            line_s=count_line_s,
        ) if complete else None
        measurements = []
        overlay_valid_track_ids: set[int] = set()
        temporal_times = () if temporal is None else temporal.event_times_s
        temporal_assignments = None
        tracker_only = []
        event_aliases = {}
        if temporal is not None and temporal.event_times_s:
            temporal_assignments, tracker_only, event_aliases = self._temporal_event_assignments(
                tracks,
                temporal.event_times_s,
                count_line_s,
                flow,
                source.metadata.fps,
                typical_length,
            )
            if temporal_assignments is not None:
                temporal_times = tuple(float(item[0]) for item in temporal_assignments)
            overlays = self._remap_overlays(overlays, event_aliases)

        line_distance = abs(line_b_s - line_a_s)
        next_event_id = 1
        if temporal_assignments is not None:
            # Event-centric mode: each temporal crossing becomes exactly one
            # counted event.  A track is supporting evidence, not the count
            # itself, so one incorrectly merged trajectory cannot erase several
            # physically separate crossings.
            for event_time, primary, geometry_sources in temporal_assignments:
                direct_geometry = self._event_frame_geometry(
                    source, detector, event_time, flow, count_line_s, typical_length
                )
                if primary is None:
                    measurement = DropletMeasurement(
                        droplet_id=next_event_id,
                        timestamp_s=float(event_time),
                        frame_index=int(round(float(event_time) * source.metadata.fps)),
                        length_px=None,
                        width_px=None,
                        area_px2=None,
                        aspect_ratio=None,
                        velocity_px_s=None,
                        spacing_px=None,
                        method="temporal",
                        valid=False,
                        rejection_reason="Temporal crossing without stable track geometry.",
                        counted=True,
                        confidence_score=0.45,
                        observation_count=0,
                    )
                    measurement = self._apply_event_geometry_repair(
                        measurement, direct_geometry, typical_length, config.calibration, stable_length_reference
                    )
                else:
                    measurement = track_to_measurement(
                        primary,
                        flow,
                        source.metadata.fps,
                        line_distance,
                        config.calibration,
                        count_line_s,
                        geometry_tracks=geometry_sources,
                        event_time_s=float(event_time),
                        reference_length_px=typical_length,
                    )
                    if measurement is None:
                        measurement = DropletMeasurement(
                            droplet_id=next_event_id,
                            timestamp_s=float(event_time),
                            frame_index=int(round(float(event_time) * source.metadata.fps)),
                            length_px=None,
                            width_px=None,
                            area_px2=None,
                            aspect_ratio=None,
                            velocity_px_s=None,
                            spacing_px=None,
                            method="temporal",
                            valid=False,
                            rejection_reason="Temporal crossing without usable geometry.",
                            counted=True,
                            confidence_score=0.45,
                            observation_count=len(primary.samples),
                        )
                    measurement = self._apply_event_geometry_repair(
                        measurement, direct_geometry, typical_length, config.calibration, stable_length_reference
                    )
                    if measurement.valid:
                        overlay_valid_track_ids.add(primary.track_id)
                        for source_track in geometry_sources or [primary]:
                            overlay_valid_track_ids.add(source_track.track_id)
                        measurement.droplet_id = next_event_id
                measurements.append(measurement)
                next_event_id += 1

            # The thin temporal signal can miss a real event in a glare band.
            # Preserve only high-confidence tracker crossings that are clearly
            # separated from every temporal event.
            for primary in tracker_only:
                event_time = float(primary.count_crossing_time)
                measurement = track_to_measurement(
                    primary,
                    flow,
                    source.metadata.fps,
                    line_distance,
                    config.calibration,
                    count_line_s,
                    geometry_tracks=[primary],
                    event_time_s=event_time,
                    reference_length_px=typical_length,
                )
                if measurement is None:
                    continue
                direct_geometry = None
                if typical_length and (
                    measurement.length_px is None
                    or measurement.length_px < typical_length * 0.78
                    or measurement.length_px > typical_length * 1.42
                ):
                    direct_geometry = self._event_frame_geometry(
                        source, detector, event_time, flow, count_line_s, typical_length
                    )
                measurement = self._apply_event_geometry_repair(
                    measurement,
                    direct_geometry,
                    typical_length,
                    config.calibration,
                    stable_length_reference,
                )
                measurement.droplet_id = next_event_id
                next_event_id += 1
                measurements.append(measurement)
                if measurement.valid:
                    overlay_valid_track_ids.add(primary.track_id)
        else:
            # Conservative fallback when the independent temporal signal is not
            # trustworthy for this video.
            for track in tracks:
                measurement = track_to_measurement(
                    track,
                    flow,
                    source.metadata.fps,
                    line_distance,
                    config.calibration,
                    count_line_s,
                    geometry_tracks=[track],
                    event_time_s=track.count_crossing_time,
                    reference_length_px=typical_length,
                )
                if measurement is not None:
                    direct_geometry = None
                    if typical_length and (
                        measurement.length_px is None
                        or measurement.length_px < typical_length * 0.78
                        or measurement.length_px > typical_length * 1.42
                    ):
                        direct_geometry = self._event_frame_geometry(
                            source, detector, measurement.timestamp_s, flow, count_line_s, typical_length
                        )
                    measurement = self._apply_event_geometry_repair(
                        measurement,
                        direct_geometry,
                        typical_length,
                        config.calibration,
                        stable_length_reference,
                    )
                    measurements.append(measurement)
                    if measurement.valid:
                        overlay_valid_track_ids.add(track.track_id)
        measurements.sort(key=lambda item: item.timestamp_s)
        self._add_spacing(measurements, config)

        summary = self._build_summary(measurements, temporal_times, source.metadata.fps)
        valid_ids = overlay_valid_track_ids
        for items in overlays.values():
            for overlay in items:
                overlay.valid_candidate = overlay.droplet_id in valid_ids

        return AnalysisResult(
            metadata=source.metadata,
            config=config,
            flow_direction=flow,
            roi_px=roi_px,
            line_a_s=line_a_s,
            line_b_s=line_b_s,
            count_line_s=count_line_s,
            measurements=measurements,
            summary=summary,
            overlays=overlays,
            complete=complete,
            processed_frames=processed_frames,
            cache_key=compute_cache_key(source.metadata.path, cache_config),
        )

    @staticmethod
    def _wall_band(profile: np.ndarray, center: float, dimension: int) -> tuple[float, float] | None:
        """Estimate two channel walls around a motion-derived centre.

        The result is used only when both walls are clearly prominent; otherwise
        Auto mode falls back to the conservative V66 band.
        """
        if profile.size < 16:
            return None
        values = np.asarray(profile, dtype=float)
        median = max(1e-6, float(np.median(values)))
        peaks = [
            i for i in range(1, len(values) - 1)
            if values[i] >= values[i - 1] and values[i] >= values[i + 1] and values[i] >= median * 1.25
        ]
        max_sep = max(70.0, dimension * 0.18)
        min_sep = max(16.0, dimension * 0.018)
        best = None
        best_score = -float("inf")
        for left in peaks:
            if left >= center - 5:
                continue
            for right in peaks:
                if right <= center + 5:
                    continue
                sep = float(right - left)
                if sep < min_sep or sep > max_sep:
                    continue
                mid_error = abs((left + right) * 0.5 - center)
                score = (values[left] + values[right]) / median - mid_error / max(12.0, sep)
                if score > best_score:
                    best_score = score
                    best = (float(left), float(right))
        if best is None or best_score < 2.65:
            return None
        left, right = best
        margin = max(14.0, (right - left) * 0.28)
        low = max(0.0, left - margin)
        high = min(float(dimension), right + margin)
        if high - low < 60.0:
            mid = (low + high) * 0.5
            low = max(0.0, mid - 30.0)
            high = min(float(dimension), mid + 30.0)
        return low, high

    def _auto_channel_roi(
        self,
        source: VideoSource,
        detector: DropletDetector,
        flow: tuple[float, float],
        config: AnalysisConfig,
    ) -> tuple[float, float, float, float] | None:
        """Find the active channel band when Auto mode starts from full frame.

        Microscope recordings often contain screws, junctions or illumination
        edges far away from the microfluidic channel.  A full-frame detector
        wastes most of its work on those pixels and can even infer flow from a
        moving highlight.  V66 uses the median background's long edge profile
        plus sampled motion centroids to derive a conservative horizontal or
        vertical band.  Explicit user ROIs are never changed.
        """

        if str(config.mode).lower() != "auto":
            return None
        x, y, w, h = config.normalized_roi()
        if max(abs(x), abs(y), abs(w - 1.0), abs(h - 1.0)) > 1e-6:
            return None

        bg = detector.context.background_gray
        if bg.size == 0:
            return None
        gx = np.abs(cv2.Sobel(bg, cv2.CV_32F, 1, 0, ksize=3))
        gy = np.abs(cv2.Sobel(bg, cv2.CV_32F, 0, 1, ksize=3))
        row_profile = gy.mean(axis=1)
        col_profile = gx.mean(axis=0)
        if len(row_profile) >= 5:
            row_profile = np.convolve(row_profile, np.ones(5) / 5.0, mode="same")
        if len(col_profile) >= 5:
            col_profile = np.convolve(col_profile, np.ones(5) / 5.0, mode="same")
        row_med = max(1e-6, float(np.median(row_profile)))
        col_med = max(1e-6, float(np.median(col_profile)))
        row_prominence = float(np.max(row_profile)) / row_med
        col_prominence = float(np.max(col_profile)) / col_med

        horizontal = abs(flow[0]) >= 0.65
        vertical = abs(flow[1]) >= 0.65
        # A clearly stronger horizontal wall profile can overrule a suspicious
        # vertical motion estimate caused by a narrow microscope obstruction.
        if vertical and row_prominence >= 1.7 and row_prominence > col_prominence * 1.03:
            horizontal, vertical = True, False
        elif horizontal and col_prominence >= 1.9 and col_prominence > row_prominence * 1.45:
            horizontal, vertical = False, True
        if not horizontal and not vertical:
            return None

        count = source.metadata.frame_count
        indices = np.linspace(0, max(0, count - 1), min(32, max(8, count)), dtype=int)
        centers: list[float] = []
        if horizontal:
            peak = float(int(np.argmax(row_profile)))
            half = max(45.0, source.metadata.height * 0.10)
            for index in indices:
                try:
                    detections = detector.detect(source.read_frame(int(index)))
                except Exception:
                    continue
                for item in detections:
                    cy = float(item.centroid[1])
                    if abs(cy - peak) <= half * 1.35:
                        centers.append(cy)
            center = float(np.median(centers)) if len(centers) >= 3 else peak
            walls = self._wall_band(row_profile, center, source.metadata.height)
            if walls is not None:
                y0, y1 = walls
            else:
                half = max(45.0, source.metadata.height * 0.10)
                y0 = max(0.0, center - half)
                y1 = min(float(source.metadata.height), center + half)
            if y1 - y0 < 60:
                return None
            return (0.0, y0 / source.metadata.height, 1.0, (y1 - y0) / source.metadata.height)

        peak = float(int(np.argmax(col_profile)))
        half = max(45.0, source.metadata.width * 0.10)
        for index in indices:
            try:
                detections = detector.detect(source.read_frame(int(index)))
            except Exception:
                continue
            for item in detections:
                cx = float(item.centroid[0])
                if abs(cx - peak) <= half * 1.35:
                    centers.append(cx)
        center = float(np.median(centers)) if len(centers) >= 3 else peak
        walls = self._wall_band(col_profile, center, source.metadata.width)
        if walls is not None:
            x0, x1 = walls
        else:
            x0 = max(0.0, center - half)
            x1 = min(float(source.metadata.width), center + half)
        if x1 - x0 < 60:
            return None
        return (x0 / source.metadata.width, 0.0, (x1 - x0) / source.metadata.width, 1.0)

    @staticmethod
    def _deduplicate_detections(detections):
        """Suppress multiple motion seeds that refine to the same droplet.

        Translucent droplets often generate a leading-edge and trailing-edge
        motion contour.  The V66 Lab refinement can correctly expand both
        seeds onto (nearly) the same outer envelope; without this NMS step the
        tracker would create two IDs for one physical droplet.  Suppression is
        based on strong bounding-box overlap, not merely centroid distance, so
        legitimately close neighbouring droplets remain separate.
        """

        if len(detections) < 2:
            return detections

        ranked = sorted(
            detections,
            key=lambda item: (bool(getattr(item, "refined_geometry", False)), item.area_px2),
            reverse=True,
        )
        kept = []
        for candidate in ranked:
            duplicate = False
            x1, y1, w1, h1 = candidate.bbox
            a1 = max(1.0, float(w1 * h1))
            for accepted in kept:
                x2, y2, w2, h2 = accepted.bbox
                ix0, iy0 = max(x1, x2), max(y1, y2)
                ix1, iy1 = min(x1 + w1, x2 + w2), min(y1 + h1, y2 + h2)
                iw, ih = max(0, ix1 - ix0), max(0, iy1 - iy0)
                intersection = float(iw * ih)
                if intersection <= 0:
                    continue
                a2 = max(1.0, float(w2 * h2))
                iou = intersection / max(1.0, a1 + a2 - intersection)
                containment = intersection / min(a1, a2)
                if iou >= 0.35 or containment >= 0.60:
                    duplicate = True
                    break
            if not duplicate:
                kept.append(candidate)
        # Keep deterministic detector order for tracker tie behaviour.
        return sorted(kept, key=lambda item: (item.centroid[0], item.centroid[1]))

    def _config_for_video(self, source: VideoSource) -> AnalysisConfig:
        # Copy through serialization so callers' configuration is never mutated.
        config = AnalysisConfig.from_dict(self.config.to_dict())
        if config.calibration.calibrated and not config.calibration.compatible_with(
            source.metadata.width, source.metadata.height
        ):
            config.calibration.um_per_px = None
            config.calibration.source = "pixels"
            config.calibration.resolution = (source.metadata.width, source.metadata.height)
        return config

    def _estimate_flow_direction(self, source: VideoSource, detector: DropletDetector) -> tuple[float, float]:
        """Estimate direction from short adjacent-frame blocks, then contour orientation.

        The sign comes from actual centroid motion whenever possible.  The
        orientation fallback is useful for slower recordings where a droplet
        barely moves between adjacent frames.
        """

        count = source.metadata.frame_count
        if count < 2:
            return (1.0, 0.0)
        block_starts = sorted({0, max(0, count // 3), max(0, 2 * count // 3)})
        motion_vectors: list[tuple[float, float]] = []
        orientations: list[tuple[float, float]] = []
        for start in block_starts:
            previous = None
            for index in range(start, min(count, start + 8)):
                try:
                    frame = source.read_frame(index)
                except Exception:
                    continue
                current = detector.detect(frame)
                orientations.extend([d.major_axis for d in current if d.major_axis is not None])
                if previous:
                    used: set[int] = set()
                    for before in previous:
                        best_index = None
                        best_distance = float("inf")
                        for candidate_index, after in enumerate(current):
                            if candidate_index in used:
                                continue
                            dx = after.centroid[0] - before.centroid[0]
                            dy = after.centroid[1] - before.centroid[1]
                            distance = math.hypot(dx, dy)
                            if 0.35 < distance < 120.0 and distance < best_distance:
                                best_index = candidate_index
                                best_distance = distance
                        if best_index is not None:
                            after = current[best_index]
                            used.add(best_index)
                            motion_vectors.append(
                                (after.centroid[0] - before.centroid[0], after.centroid[1] - before.centroid[1])
                            )
                previous = current
        if motion_vectors:
            vectors = np.asarray(motion_vectors, dtype=float)
            norms = np.linalg.norm(vectors, axis=1)
            vectors = vectors[norms > 0.35]
            if len(vectors):
                median = np.median(vectors, axis=0)
                if np.linalg.norm(median) > 0.35:
                    return _unit((float(median[0]), float(median[1])))
        if orientations:
            # Axis orientation has 180-degree ambiguity. Average doubled angles.
            angles = np.asarray([math.atan2(v[1], v[0]) for v in orientations], dtype=float)
            x = float(np.mean(np.cos(2.0 * angles)))
            y = float(np.mean(np.sin(2.0 * angles)))
            angle = 0.5 * math.atan2(y, x)
            vector = (math.cos(angle), math.sin(angle))
            if vector[0] < 0:
                vector = (-vector[0], -vector[1])
            return _unit(vector)
        # Most current lab recordings show a horizontal channel.
        return (1.0, 0.0)

    @staticmethod
    def _remap_overlays(overlays, id_map):
        if not id_map:
            return overlays
        for items in overlays.values():
            for overlay in items:
                overlay.droplet_id = int(id_map.get(overlay.droplet_id, overlay.droplet_id))
        return overlays

    @staticmethod
    def _artifact_line_penalty(artifact_mask, roi_px, flow, s_value: float) -> float:
        if artifact_mask is None or getattr(artifact_mask, "size", 0) == 0:
            return 0.0
        x0, y0, w, h = roi_px
        yy, xx = np.indices((h, w), dtype=np.float32)
        projection = (xx + x0) * float(flow[0]) + (yy + y0) * float(flow[1])
        line_pixels = np.abs(projection - float(s_value)) <= 2.5
        count = int(np.count_nonzero(line_pixels))
        if count <= 0:
            return 0.0
        return float(np.count_nonzero((artifact_mask > 0) & line_pixels) / count)

    @staticmethod
    def _choose_analysis_lines(tracks, roi_px, flow, fps: float, artifact_mask=None) -> tuple[float, float, float]:
        """Choose count and velocity lines as separate optimisation problems.

        The count line needs a clean region crossed by many tracks.  Velocity
        benefits from a much wider A/B baseline, so V67 explicitly rewards
        pairs separated by roughly 20-45% of the usable channel span instead of
        placing both lines only seven percent apart as V66 did.
        """

        min_s, max_s = _projection_bounds(roi_px, flow)
        span = max(1.0, max_s - min_s)
        minimum_samples = max(3, int(round(max(1.0, fps) * 0.10)))
        eligible = []
        for track in tracks:
            if len(track.samples) < minimum_samples:
                continue
            positions = np.asarray([
                sample.detection.centroid[0] * flow[0] + sample.detection.centroid[1] * flow[1]
                for sample in track.samples
            ], dtype=float)
            if positions.size < 2:
                continue
            low, high = float(np.min(positions)), float(np.max(positions))
            if high - low < span * 0.025:
                continue
            eligible.append((low, high))

        fractions = np.linspace(0.18, 0.82, 49)
        line_data = []
        for fraction in fractions:
            value = min_s + span * float(fraction)
            coverage = sum(1 for low, high in eligible if low <= value <= high)
            artifact = DropletAnalyzer._artifact_line_penalty(artifact_mask, roi_px, flow, value)
            line_data.append((float(fraction), float(value), int(coverage), float(artifact)))

        # Count line: first maximize real track coverage.  For transparent
        # droplets, forcing the count line toward the geometric centre can move
        # it into a glare/internal-reflection region even when a cleaner section
        # of the channel is crossed by more complete trajectories.  Artifact
        # overlap and centrality are therefore tie-breakers, not the primary
        # objective.
        count_candidates = [item for item in line_data if 0.30 <= item[0] <= 0.78] or line_data
        best_count = max(
            count_candidates,
            key=lambda item: (
                item[2],
                -item[3],
                -abs(item[0] - 0.50),
            ),
        )
        count_line = best_count[1]

        # Velocity pair: both lines should be spanned by the same tracks and be
        # far enough apart that a one-frame crossing uncertainty is not huge.
        best_pair = None
        best_score = -float("inf")
        for i, left in enumerate(line_data):
            for right in line_data[i + 1:]:
                separation_fraction = right[0] - left[0]
                if separation_fraction < 0.18 or separation_fraction > 0.46:
                    continue
                both = sum(1 for low, high in eligible if low <= left[1] and high >= right[1])
                if eligible and both <= 0:
                    continue
                min_coverage = min(left[2], right[2])
                artifact_penalty = left[3] + right[3]
                edge_penalty = abs((left[0] + right[0]) / 2.0 - 0.50)
                score = (
                    both * 12.0
                    + min_coverage * 2.0
                    + separation_fraction * 4.0
                    - artifact_penalty * 8.0
                    - edge_penalty * 0.5
                )
                if score > best_score:
                    best_score = score
                    best_pair = (left[1], right[1])

        if best_pair is None:
            line_a = min_s + span * 0.30
            line_b = min_s + span * 0.70
        else:
            line_a, line_b = best_pair

        # Hard safety floor: never allow the two velocity lines to collapse back
        # into the fragile V66 narrow baseline.
        if line_b - line_a < span * 0.18:
            line_a = min_s + span * 0.32
            line_b = min_s + span * 0.68
        return float(line_a), float(line_b), float(count_line)

    @staticmethod
    def _typical_track_length(tracks, flow, fps: float = 10.0) -> float | None:
        """Estimate the stable outer-envelope scale of the recording.

        Internal reflections are often short and spatially local.  The most
        useful reference therefore comes from coherent tracks that travel a
        meaningful distance through the channel.  We use an upper robust
        percentile, not the largest contour, so glare cannot define the scale.
        """
        values: list[float] = []
        minimum_samples = max(5, int(round(max(1.0, fps) * 0.45)))
        for track in tracks:
            if len(track.samples) < minimum_samples or track_confidence(track, flow) < 0.66:
                continue
            usable = [
                sample for sample in track.samples
                if sample.detection.length_px is not None
                and sample.detection.width_px is not None
                and not sample.detection.touches_roi_edge
                and sample.detection.artifact_overlap <= 0.52
            ]
            refined = [sample for sample in usable if sample.detection.refined_geometry]
            if len(refined) >= 2:
                usable = refined
            if len(usable) < 2:
                continue
            lengths = np.asarray([float(s.detection.length_px) for s in usable], dtype=float)
            median_length = float(np.median(lengths))
            positions = [
                sample.detection.centroid[0] * flow[0]
                + sample.detection.centroid[1] * flow[1]
                for sample in track.samples
            ]
            movement = (max(positions) - min(positions)) if positions else 0.0
            if movement < max(45.0, median_length * 1.15):
                continue
            if median_length > 6.0 and math.isfinite(median_length):
                values.append(median_length)
        if not values:
            return None
        data = np.asarray(values, dtype=float)
        if data.size >= 5:
            # The 75th percentile intentionally favors the persistent outer
            # envelope over shorter internal-reflection tracks, while remaining
            # far less sensitive to one giant glare contour than max().
            reference = float(np.percentile(data, 75.0))
        else:
            reference = float(np.median(data))
        return reference if math.isfinite(reference) and reference > 1.0 else None

    @staticmethod
    def _stable_recording_length_reference(tracks, flow, fps: float = 10.0) -> float | None:
        """Return a conservative length reference only for a true stable mode.

        ``_typical_track_length`` is intentionally allowed to favor an outer
        envelope even on bimodal recordings such as ``164623``; that makes it a
        useful *repair* hint.  It is not safe to use that hint to reject data.
        Rejection is enabled only when many coherent tracks form a statistically
        tight recording-level size mode.
        """
        values: list[float] = []
        minimum_samples = max(5, int(round(max(1.0, fps) * 0.45)))
        for track in tracks:
            if len(track.samples) < minimum_samples or track_confidence(track, flow) < 0.66:
                continue
            usable = [
                sample for sample in track.samples
                if sample.detection.length_px is not None
                and sample.detection.width_px is not None
                and not sample.detection.touches_roi_edge
                and sample.detection.artifact_overlap <= 0.52
            ]
            refined = [sample for sample in usable if sample.detection.refined_geometry]
            if len(refined) >= 2:
                usable = refined
            if len(usable) < 2:
                continue
            lengths = np.asarray([float(s.detection.length_px) for s in usable], dtype=float)
            median_length = float(np.median(lengths))
            positions = [
                sample.detection.centroid[0] * flow[0]
                + sample.detection.centroid[1] * flow[1]
                for sample in track.samples
            ]
            movement = (max(positions) - min(positions)) if positions else 0.0
            if movement < max(45.0, median_length * 1.15):
                continue
            if median_length > 6.0 and math.isfinite(median_length):
                values.append(median_length)

        # Four coherent tracks are enough for a short regression clip, but the
        # robust spread must be narrow.  A multimodal recording therefore keeps
        # repair assistance while disabling reference-based rejection.
        if len(values) < 4:
            return None
        data = np.asarray(values, dtype=float)
        median = float(np.median(data))
        if median <= 1.0 or not math.isfinite(median):
            return None
        mad = float(np.median(np.abs(data - median)))
        relative_mad = 0.0 if mad <= 1e-9 else 1.4826 * mad / median
        if relative_mad > 0.20:
            return None
        return median

    @staticmethod
    def _event_geometry_sources(
        tracks,
        primary_track,
        event_time: float,
        count_line_s: float,
        flow,
        fps: float,
        typical_length: float | None,
    ):
        """Choose one conservative geometry source for a counted event.

        V3.1 selected the largest trustworthy co-temporal contour.  On real
        recordings that could turn glare or a neighbouring partial contour into
        the final droplet size.  V3.2 keeps the timing track by default and only
        substitutes another track when the primary geometry is an obvious
        outlier from a stable recording mode and the replacement independently
        looks like a wider/larger physical envelope.
        """
        if not primary_track.samples:
            return [primary_track]

        tx, ty = -float(flow[1]), float(flow[0])
        time_window = max(0.32, 3.5 / max(1.0, fps))

        def profile(track):
            local = [sample for sample in track.samples if abs(sample.time_s - event_time) <= time_window]
            if not local:
                return None
            usable = [
                sample for sample in local
                if sample.detection.length_px is not None
                and sample.detection.width_px is not None
                and not sample.detection.touches_roi_edge
                and sample.detection.artifact_overlap <= 0.52
            ]
            refined = [sample for sample in usable if sample.detection.refined_geometry]
            if len(refined) >= 2:
                usable = refined
            if not usable:
                return None
            lengths = np.asarray([float(s.detection.length_px) for s in usable], dtype=float)
            widths = np.asarray([float(s.detection.width_px) for s in usable], dtype=float)
            areas = np.asarray([float(s.detection.area_px2) for s in usable], dtype=float)
            length = float(np.median(lengths))
            width = float(np.median(widths))
            area = float(np.median(areas))
            if length <= 0 or width <= 0:
                return None
            spatial = float("inf")
            for sample in usable:
                d = sample.detection
                center_s = d.centroid[0] * flow[0] + d.centroid[1] * flow[1]
                distance = abs(center_s - count_line_s)
                if d.min_s is not None and d.max_s is not None:
                    if d.min_s <= count_line_s <= d.max_s:
                        distance = 0.0
                    else:
                        distance = min(abs(d.min_s - count_line_s), abs(d.max_s - count_line_s))
                spatial = min(spatial, float(distance))
            transverse = float(np.median([
                sample.detection.centroid[0] * tx + sample.detection.centroid[1] * ty
                for sample in usable
            ]))
            variation = 0.0
            if lengths.size >= 2:
                median = float(np.median(lengths))
                mad = float(np.median(np.abs(lengths - median)))
                variation = 0.0 if median <= 1e-9 else min(2.0, 1.4826 * mad / median)
            return {
                "track": track,
                "length": length,
                "width": width,
                "area": area,
                "variation": variation,
                "confidence": track_confidence(track, flow),
                "artifact": float(np.mean([float(s.detection.artifact_overlap) for s in usable])),
                "spatial": spatial,
                "transverse": transverse,
                "observations": len(usable),
            }

        primary = profile(primary_track)
        if primary is None or typical_length is None or typical_length <= 1.0:
            return [primary_track]

        reference = float(typical_length)
        primary_ratio = float(primary["length"]) / reference
        # Broad normal band: if the timing track already looks plausible, do
        # not replace it simply because a larger contour exists nearby.
        if 0.68 <= primary_ratio <= 1.48 and float(primary["variation"]) <= 0.42:
            return [primary_track]

        candidates = []
        for track in tracks:
            if track.track_id == primary_track.track_id:
                continue
            candidate = profile(track)
            if candidate is None:
                continue
            if candidate["observations"] < 2:
                continue
            if candidate["confidence"] < 0.50 or candidate["variation"] > 0.26:
                continue
            if candidate["artifact"] > 0.42:
                continue
            ratio = float(candidate["length"]) / reference
            if ratio < 0.76 or ratio > 1.34:
                continue
            if candidate["spatial"] > max(26.0, float(candidate["length"]) * 0.52):
                continue
            transverse_limit = max(14.0, 1.10 * max(float(primary["width"]), float(candidate["width"])))
            if abs(float(candidate["transverse"]) - float(primary["transverse"])) > transverse_limit:
                continue

            if primary_ratio < 0.68:
                envelope_support = (
                    float(candidate["width"]) >= float(primary["width"]) * 1.18
                    or float(candidate["area"]) >= float(primary["area"]) * 1.38
                )
                if not envelope_support:
                    continue
            # For an oversized primary, a smaller candidate near the stable
            # mode is inherently safer; no "larger envelope" requirement.
            score = (
                abs(math.log(max(1e-6, ratio))) * 1.6
                + float(candidate["variation"]) * 0.8
                + (1.0 - float(candidate["confidence"])) * 0.45
                + float(candidate["artifact"]) * 0.35
            )
            candidates.append((score, candidate))

        if not candidates:
            return [primary_track]
        candidates.sort(key=lambda item: item[0])
        replacement = candidates[0][1]
        return [replacement["track"]]

    @staticmethod
    def _event_frame_geometry(
        source,
        detector,
        event_time: float,
        flow,
        count_line_s: float,
        typical_length: float | None,
    ):
        """Re-measure geometry directly around one count-line event.

        This is a conservative final check, not another counter.  It decouples
        size measurement from tracker fragmentation: if a timing track follows
        only an internal reflection, the exact event frames can still contain a
        clean outer contour.  A recording-level reference is required so glare
        cannot become the new envelope merely because it is large.
        """
        if typical_length is None or typical_length <= 1.0:
            return None
        fps = max(1.0, float(source.metadata.fps))
        center_frame = int(round(float(event_time) * fps))
        # The temporal signal peaks on the strongest refractive change, which
        # can lead/lag the geometric centre by several frames (especially on
        # the 10-fps recordings).  Re-measure a small symmetric time window.
        half_window_s = 0.85 if fps <= 15.0 else 0.45
        offsets_s = (-half_window_s, -half_window_s * 0.5, 0.0, half_window_s * 0.5, half_window_s)
        frame_indices = sorted({
            max(0, min(source.metadata.frame_count - 1, center_frame + int(round(offset * fps))))
            for offset in offsets_s
        })
        candidates = []
        reference = float(typical_length)
        for frame_index in frame_indices:
            try:
                frame = source.read_frame(frame_index)
            except Exception:
                continue
            detections = detector.detect(frame)
            for detection in detections:
                apply_flow_geometry(detection, flow, count_line_s)
                detector.refine_detection(frame, detection, flow)
                apply_flow_geometry(detection, flow, count_line_s)
            detections = DropletAnalyzer._deduplicate_detections(detections)
            for detection in detections:
                if detection.length_px is None or detection.width_px is None:
                    continue
                if detection.touches_roi_edge or detection.artifact_overlap > 0.55:
                    continue
                length = float(detection.length_px)
                width = float(detection.width_px)
                if length <= 0 or width <= 0:
                    continue
                ratio = length / reference
                if ratio < 0.68 or ratio > 1.42:
                    continue
                center_s = detection.centroid[0] * flow[0] + detection.centroid[1] * flow[1]
                spatial = abs(center_s - count_line_s)
                if detection.min_s is not None and detection.max_s is not None:
                    if detection.min_s <= count_line_s <= detection.max_s:
                        spatial = 0.0
                    else:
                        spatial = min(abs(detection.min_s - count_line_s), abs(detection.max_s - count_line_s))
                if spatial > max(24.0, length * 0.48):
                    continue
                closeness = abs(math.log(max(1e-6, ratio)))
                frame_error = abs(frame_index / fps - event_time)
                score = (
                    closeness * 1.8
                    + spatial / max(35.0, length) * 0.65
                    + float(detection.artifact_overlap) * 0.55
                    + frame_error * 0.18
                    - (0.10 if detection.refined_geometry else 0.0)
                )
                candidates.append((score, frame_index, detection))
        if not candidates:
            return None
        candidates.sort(key=lambda item: item[0])
        best_length = float(candidates[0][2].length_px)
        cluster = [
            item for item in candidates
            if 0.82 * best_length <= float(item[2].length_px) <= 1.22 * best_length
        ][:5]
        if not cluster:
            cluster = [candidates[0]]
        lengths = np.asarray([float(item[2].length_px) for item in cluster], dtype=float)
        widths = np.asarray([float(item[2].width_px) for item in cluster], dtype=float)
        areas = np.asarray([float(item[2].area_px2) for item in cluster], dtype=float)
        return {
            "length_px": float(np.median(lengths)),
            "width_px": float(np.median(widths)),
            "area_px2": float(np.median(areas)),
            "frame_index": int(candidates[0][1]),
            "observations": len(cluster),
        }

    @staticmethod
    def _apply_event_geometry_repair(
        measurement,
        direct_geometry,
        reference_length,
        calibration,
        stable_reference_length: float | None = None,
    ):
        """Repair suspicious geometry, then conservatively validate it.

        ``reference_length`` is a broad repair hint and may come from a
        multimodal recording.  ``stable_reference_length`` is stricter: it is
        only present when the recording has a statistically tight size mode and
        can therefore be used to *reject* unresolved geometry outliers.  Count
        and timing remain valid even when size is rejected.
        """
        reference = float(reference_length) if reference_length and reference_length > 1.0 else None
        current = measurement.length_px
        suspicious = bool(
            reference is not None
            and (
                current is None
                or current < reference * 0.78
                or current > reference * 1.42
                or (measurement.geometry_variation is not None and measurement.geometry_variation > 0.42)
            )
        )

        if suspicious and direct_geometry is not None:
            length = float(direct_geometry["length_px"])
            if reference * 0.70 <= length <= reference * 1.38:
                width = float(direct_geometry["width_px"])
                area = float(direct_geometry["area_px2"])
                measurement.length_px = length
                measurement.width_px = width
                measurement.area_px2 = area
                measurement.aspect_ratio = (length / width) if width > 1e-9 else None
                measurement.frame_index = int(direct_geometry["frame_index"])
                measurement.method = "event_geometry"
                measurement.valid = True
                measurement.rejection_reason = ""
                measurement.observation_count = max(
                    measurement.observation_count,
                    int(direct_geometry.get("observations", 1)),
                )
                measurement.confidence_score = max(measurement.confidence_score or 0.0, 0.72)
                measurement.geometry_variation = min(measurement.geometry_variation or 0.0, 0.18)
                measurement.apply_calibration(calibration)

        # A stable recording-level mode is used only as a final sanity check.
        # Keep the raw measured pixels for diagnostics/CSV, but exclude the size
        # from valid geometry statistics when recovery could not justify it.
        stable = (
            float(stable_reference_length)
            if stable_reference_length is not None and stable_reference_length > 1.0
            else None
        )
        if stable is not None and measurement.length_px is not None:
            ratio = float(measurement.length_px) / stable
            unstable_shape = (
                measurement.geometry_variation is not None
                and measurement.geometry_variation > 0.55
            )
            if ratio < 0.58 or ratio > 1.55 or unstable_shape:
                measurement.valid = False
                measurement.rejection_reason = (
                    "Geometry outside the stable recording envelope; "
                    "crossing counted but size excluded."
                )
        return measurement

    @staticmethod
    def _temporal_event_assignments(
        tracks,
        event_times,
        count_line_s,
        flow,
        fps: float,
        typical_length: float | None,
    ):
        """Reconcile temporal peaks with physical tracks one event at a time.

        The temporal signal is excellent at locating *when* something crossed a
        clean line, but transparent droplets can produce two nearby peaks from
        the same physical trajectory.  V3.3 therefore collapses nearby peaks
        only when they resolve to the same track, prevents an already-associated
        track from returning as ``tracker_only``, and rejects isolated
        tracker-only crossings far outside any active temporal burst.
        """
        event_times = tuple(float(v) for v in event_times)
        preliminary = [track for track in tracks if track.count_crossing_time is not None]
        if not event_times:
            return None, [], {}
        if preliminary:
            if len(event_times) > len(preliminary) * 1.85 + 4:
                return None, [], {}
            if len(preliminary) > len(event_times) * 2.25 + 5:
                return None, [], {}

        # Derive the duplicate guard from the recording's own event cadence.
        # Long idle gaps do not influence the median in normal recordings; the
        # cap prevents a slow generator from merging genuinely separate events.
        diffs = np.diff(np.asarray(event_times, dtype=float)) if len(event_times) >= 2 else np.asarray([])
        positive = diffs[diffs > max(1e-6, 0.5 / max(1.0, fps))]
        if positive.size:
            local = positive[positive <= min(12.0, float(np.percentile(positive, 85.0)))]
            cadence = float(np.median(local if local.size else positive))
        else:
            cadence = 1.2
        duplicate_window = max(0.90, min(1.80, cadence * 0.75))

        tx, ty = -float(flow[1]), float(flow[0])
        time_window = max(0.72, 6.0 / max(1.0, fps))
        assignments = []
        used_local_windows: dict[int, float] = {}

        for event_time in event_times:
            candidates = []
            for track in tracks:
                nearby = [sample for sample in track.samples if abs(sample.time_s - event_time) <= time_window]
                if not nearby:
                    continue
                usable = [
                    sample for sample in nearby
                    if sample.detection.length_px is not None
                    and sample.detection.width_px is not None
                    and not sample.detection.touches_roi_edge
                    and sample.detection.artifact_overlap <= 0.60
                ]
                if not usable:
                    continue
                lengths = np.asarray([float(s.detection.length_px) for s in usable], dtype=float)
                widths = np.asarray([float(s.detection.width_px) for s in usable], dtype=float)
                median_length = float(np.median(lengths))
                median_width = float(np.median(widths))
                best_spatial = float("inf")
                time_error = float("inf")
                for sample in usable:
                    d = sample.detection
                    center_s = d.centroid[0] * flow[0] + d.centroid[1] * flow[1]
                    distance = abs(center_s - count_line_s)
                    if d.min_s is not None and d.max_s is not None:
                        if d.min_s <= count_line_s <= d.max_s:
                            distance = 0.0
                        else:
                            distance = min(abs(d.min_s - count_line_s), abs(d.max_s - count_line_s))
                    if distance < best_spatial:
                        best_spatial = float(distance)
                        time_error = abs(sample.time_s - event_time)
                if best_spatial > max(42.0, median_length * 1.05):
                    continue
                confidence = track_confidence(track, flow)
                if confidence < 0.38:
                    continue
                length_score = 0.55
                if typical_length and median_length > 0:
                    ratio = median_length / typical_length
                    if ratio < 0.20 or ratio > 2.25:
                        continue
                    length_score = math.exp(-abs(math.log(max(1e-6, ratio))) / 0.58)
                refined_fraction = float(np.mean([bool(s.detection.refined_geometry) for s in usable]))
                spatial_score = max(0.0, 1.0 - best_spatial / max(42.0, median_length * 1.05))
                temporal_score = max(0.0, 1.0 - time_error / time_window)
                observation_score = min(1.0, len(usable) / 4.0)
                reuse_penalty = 0.0
                previous_event = used_local_windows.get(track.track_id)
                if previous_event is not None and abs(event_time - previous_event) < time_window * 1.35:
                    reuse_penalty = 0.8
                score = (
                    confidence * 1.25
                    + length_score * 0.75
                    + spatial_score * 0.55
                    + temporal_score * 0.45
                    + refined_fraction * 0.18
                    + observation_score * 0.22
                    - reuse_penalty
                )
                candidates.append((score, track, median_length, median_width))

            if not candidates:
                assignments.append((event_time, None, []))
                continue
            candidates.sort(key=lambda item: item[0], reverse=True)
            primary = candidates[0][1]
            used_local_windows[primary.track_id] = event_time
            sources = DropletAnalyzer._event_geometry_sources(
                tracks,
                primary,
                event_time,
                count_line_s,
                flow,
                fps,
                typical_length,
            )
            assignments.append((event_time, primary, sources))

        # A temporal peak can land slightly after the strongest geometric
        # crossing.  For otherwise unsupported peaks, attach a nearby coherent
        # crossing before calling the event geometry-less.  This is intentionally
        # limited to the duplicate window and does not search across long gaps.
        already_primary = {primary.track_id for _, primary, _ in assignments if primary is not None}
        repaired_assignments = []
        for event_time, primary, sources in assignments:
            if primary is not None:
                repaired_assignments.append((event_time, primary, sources))
                continue
            fallback = []
            for track in preliminary:
                if track.track_id in already_primary:
                    continue
                crossing = float(track.count_crossing_time)
                delta = abs(crossing - event_time)
                if delta > duplicate_window:
                    continue
                confidence = track_confidence(track, flow)
                if len(track.samples) < 3 or confidence < 0.62:
                    continue
                fallback.append((delta, -confidence, track))
            if not fallback:
                repaired_assignments.append((event_time, None, []))
                continue
            fallback.sort(key=lambda item: (item[0], item[1]))
            primary = fallback[0][2]
            already_primary.add(primary.track_id)
            sources = DropletAnalyzer._event_geometry_sources(
                tracks, primary, event_time, count_line_s, flow, fps, typical_length
            )
            repaired_assignments.append((event_time, primary, sources))
        assignments = repaired_assignments

        # Collapse only peaks that resolve to the *same* physical track.  A
        # neighbouring track is always retained even if the generator is fast.
        collapsed = []
        last_index_by_track: dict[int, int] = {}
        for item in assignments:
            event_time, primary, sources = item
            if primary is None:
                collapsed.append(item)
                continue
            previous_index = last_index_by_track.get(primary.track_id)
            if previous_index is not None:
                previous_time, previous_primary, _ = collapsed[previous_index]
                if abs(event_time - previous_time) <= duplicate_window:
                    crossing = primary.count_crossing_time
                    if crossing is not None and abs(event_time - crossing) < abs(previous_time - crossing):
                        collapsed[previous_index] = item
                    continue
            last_index_by_track[primary.track_id] = len(collapsed)
            collapsed.append(item)
        assignments = collapsed

        # Rebuild aliases only from events that survived deduplication.
        alias_map: dict[int, int] = {}
        used_track_ids: set[int] = set()
        for _, primary, sources in assignments:
            if primary is None:
                continue
            used_track_ids.add(primary.track_id)
            for source in sources:
                used_track_ids.add(source.track_id)
                if source.track_id != primary.track_id:
                    alias_map[source.track_id] = primary.track_id

        surviving_times = tuple(float(event_time) for event_time, _, _ in assignments)
        tracker_only = []
        if len(surviving_times) >= 2:
            intervals = np.diff(np.asarray(surviving_times, dtype=float))
            active_intervals = intervals[(intervals > duplicate_window) & (intervals < 12.0)]
            active_period = float(np.median(active_intervals)) if active_intervals.size else cadence
        else:
            active_period = cadence
        active_radius = max(3.0, min(8.0, active_period * 2.5))

        for track in preliminary:
            if track.track_id in used_track_ids:
                continue
            crossing = float(track.count_crossing_time)
            nearest = min(abs(crossing - event) for event in surviving_times) if surviving_times else float("inf")
            # Near an existing event it is another representation of that event;
            # far from every temporal burst it is an isolated tracker artefact.
            if nearest <= duplicate_window or nearest > active_radius:
                continue
            if len(track.samples) < 3 or track_confidence(track, flow) < 0.66:
                continue
            tracker_only.append(track)

        return assignments, tracker_only, alias_map

    @staticmethod
    def _select_temporal_count_tracks(
        tracks,
        event_times,
        count_line_s,
        flow,
        fps: float,
        typical_length: float | None = None,
    ):
        """Reconcile tracker crossings with the independent temporal signal.

        Temporal events now *deduplicate and correct* tracker crossings instead
        of replacing the entire tracker count.  This fixes the V3.1 regression
        on long recordings where the temporal detector saw ~65 events but only
        46 survived the global one-to-one override.  Unmatched, coherent tracker
        crossings are preserved because the thin temporal line can miss a real
        droplet in glare.
        """
        event_times = tuple(float(v) for v in event_times)
        preliminary = [track for track in tracks if track.count_crossing_time is not None]
        if not event_times or not preliminary:
            return {}, {}

        # Reject temporal output only when it is wildly inconsistent.  In the
        # normal case the two sources should be in the same order of magnitude.
        if len(event_times) > len(preliminary) * 1.75 + 3:
            return {}, {}
        if len(preliminary) > len(event_times) * 2.10 + 4:
            return {}, {}

        tolerance = max(0.90, 6.0 / max(1.0, fps))
        groups: dict[int, list] = {i: [] for i in range(len(event_times))}
        unmatched_tracks = []
        for track in preliminary:
            crossing = float(track.count_crossing_time)
            distances = [abs(crossing - event) for event in event_times]
            nearest = int(np.argmin(np.asarray(distances, dtype=float)))
            error = float(distances[nearest])
            if error <= tolerance:
                groups[nearest].append((track, error))
            else:
                unmatched_tracks.append(track)

        selected: dict[int, object] = {}
        duplicate_ids: set[int] = set()
        for event_index, options in groups.items():
            if not options:
                continue
            ranked = []
            for track, error in options:
                confidence = track_confidence(track, flow)
                lengths = [float(s.detection.length_px) for s in track.samples if s.detection.length_px]
                median_length = float(np.median(lengths)) if lengths else None
                length_score = 0.5
                if typical_length and median_length and median_length > 0:
                    ratio = median_length / typical_length
                    length_score = math.exp(-abs(math.log(max(1e-6, ratio))) / 0.55)
                observation_score = min(1.0, len(track.samples) / 7.0)
                score = (
                    confidence * 1.35
                    + length_score * 0.55
                    + observation_score * 0.30
                    + max(0.0, 1.0 - error / tolerance) * 0.55
                )
                ranked.append((score, track, error))
            ranked.sort(key=lambda item: item[0], reverse=True)
            winner = ranked[0][1]
            selected[event_index] = winner
            for _, loser, _ in ranked[1:]:
                duplicate_ids.add(loser.track_id)

        # A temporal event without a centroid crossing may still have a track
        # whose segmented envelope occupies the count region.  Recover only a
        # strong, unused candidate and never invent geometry from the signal.
        used_ids = {track.track_id for track in selected.values()}
        recover_window = max(0.60, 5.0 / max(1.0, fps))
        for event_index, event_time in enumerate(event_times):
            if event_index in selected:
                continue
            candidates = []
            for track in tracks:
                if track.track_id in used_ids or track.track_id in duplicate_ids:
                    continue
                nearby = [s for s in track.samples if abs(s.time_s - event_time) <= recover_window]
                if not nearby:
                    continue
                best_spatial = float("inf")
                for sample in nearby:
                    d = sample.detection
                    center_s = d.centroid[0] * flow[0] + d.centroid[1] * flow[1]
                    distance = abs(center_s - count_line_s)
                    if d.min_s is not None and d.max_s is not None and d.min_s <= count_line_s <= d.max_s:
                        distance = 0.0
                    best_spatial = min(best_spatial, float(distance))
                lengths = [float(s.detection.length_px) for s in nearby if s.detection.length_px]
                median_length = float(np.median(lengths)) if lengths else 30.0
                if best_spatial > max(30.0, median_length * 0.80):
                    continue
                confidence = track_confidence(track, flow)
                if confidence < 0.58 or len(nearby) < 2:
                    continue
                score = confidence + min(0.35, len(nearby) * 0.05) - best_spatial / max(80.0, median_length * 3.0)
                candidates.append((score, track))
            if candidates:
                candidates.sort(key=lambda item: item[0], reverse=True)
                winner = candidates[0][1]
                selected[event_index] = winner
                used_ids.add(winner.track_id)

        # Only tracks that compete for the same temporal event are suppressed.
        # Coherent tracker-only crossings are kept; they cover temporal misses.
        for track in tracks:
            if track.track_id in duplicate_ids:
                track.count_crossing_time = None

        geometry_by_track: dict[int, list] = {}
        primary_ids = {track.track_id for track in selected.values()}
        alias_map: dict[int, int] = {}
        for event_index, primary in selected.items():
            event_time = float(event_times[event_index])
            primary.count_crossing_time = event_time
            sources = DropletAnalyzer._event_geometry_sources(
                tracks,
                primary,
                event_time,
                count_line_s,
                flow,
                fps,
                typical_length,
            )
            geometry_by_track[primary.track_id] = sources
            for source in sources:
                if source.track_id != primary.track_id and source.track_id not in primary_ids:
                    alias_map[source.track_id] = primary.track_id

        # Preserve tracker-only events that did not collide with a temporal
        # event, provided the trajectory has minimally usable support.
        for track in unmatched_tracks:
            if track.track_id in duplicate_ids:
                continue
            if len(track.samples) >= 3 and track_confidence(track, flow) >= 0.50:
                geometry_by_track.setdefault(track.track_id, [track])
            else:
                track.count_crossing_time = None

        return geometry_by_track, alias_map

    @staticmethod
    def _add_spacing(measurements, config: AnalysisConfig) -> None:
        previous = None
        for measurement in measurements:
            if not measurement.valid:
                continue
            if previous is not None and measurement.velocity_px_s is not None:
                dt = max(0.0, measurement.timestamp_s - previous.timestamp_s)
                measurement.spacing_px = float(measurement.velocity_px_s * dt)
                measurement.spacing_um = config.calibration.length_um(measurement.spacing_px)
            previous = measurement

    @staticmethod
    def _build_summary(measurements, temporal_event_times=(), fps: float = 0.0) -> AnalysisSummary:
        counted = [item for item in measurements if item.counted]
        valid = [item for item in counted if item.valid and item.length_px is not None]
        # Median is more representative than mean when a single reflection makes
        # one segmented envelope unusually long.  The existing UI label stays
        # unchanged; only the estimator behind it becomes robust.
        mean_px = float(np.median([item.length_px for item in valid])) if valid else None
        calibrated_lengths = [item.length_um for item in valid if item.length_um is not None]
        mean_um = float(np.median(calibrated_lengths)) if calibrated_lengths else None

        # Generation timing is an event property, not a geometry property.
        # Counted crossings with rejected size measurements still contribute to
        # the inter-droplet period.
        times = np.asarray([float(item.timestamp_s) for item in counted], dtype=float)
        intervals = np.diff(times) if len(times) >= 2 else np.asarray([], dtype=float)
        core = intervals[intervals > max(1e-6, 0.5 / max(1.0, fps))]
        period = None
        period_mad = None
        if core.size:
            median = float(np.median(core))
            mad = float(np.median(np.abs(core - median)))
            # Remove long pauses/missed detections from the steady generator
            # statistic while retaining genuine interval variation.
            limit = max(median * 2.5, median + 4.0 * 1.4826 * mad)
            filtered = core[core <= limit]
            if filtered.size:
                period = float(np.median(filtered))
                period_mad = float(np.median(np.abs(filtered - period)))
        rate = (1.0 / period) if period is not None and period > 1e-9 else None

        temporal_times = tuple(float(v) for v in temporal_event_times)
        count_confidence = None
        if counted:
            if temporal_times:
                track_times = [float(item.timestamp_s) for item in counted]
                tolerance = max(0.18, 2.2 / max(1.0, fps))
                matched, _, _ = match_event_times(track_times, temporal_times, tolerance_s=tolerance)
                agreement = matched / max(1, max(len(track_times), len(temporal_times)))
                track_quality = float(np.mean([item.confidence_score or 0.0 for item in counted]))
                count_confidence = float(max(0.0, min(1.0, 0.62 * agreement + 0.38 * track_quality)))
            else:
                count_confidence = float(np.mean([item.confidence_score or 0.0 for item in counted]))

        return AnalysisSummary(
            total_droplets=len(counted),
            valid_droplets=len(valid),
            mean_length_px=mean_px,
            mean_length_um=mean_um,
            generation_rate_s=rate,
            generation_period_s=period,
            generation_period_mad_s=period_mad,
            temporal_event_count=len(temporal_times),
            count_confidence=count_confidence,
        )
