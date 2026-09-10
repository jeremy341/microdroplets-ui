"""Flow-aware multi-droplet tracking and V67 track-level post-processing.

The tracker intentionally keeps frame matching lightweight.  The stronger
reasoning happens after the first pass, when the full recording is available:
short fragments can be stitched, geometry can be aggregated over many frames,
and confidence can be estimated from trajectory/shape stability instead of a
single contour.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math

import numpy as np

from .models import Calibration, Detection, DropletMeasurement, OverlayDetection


@dataclass
class TrackSample:
    frame_index: int
    time_s: float
    detection: Detection


@dataclass
class Track:
    track_id: int
    samples: list[TrackSample] = field(default_factory=list)
    missed_frames: int = 0
    velocity_frame: tuple[float, float] = (0.0, 0.0)
    crossing_a_time: float | None = None
    crossing_b_time: float | None = None
    count_crossing_time: float | None = None
    first_a_intersection: float | None = None
    last_a_intersection: float | None = None

    @property
    def last_sample(self) -> TrackSample:
        return self.samples[-1]

    @property
    def first_sample(self) -> TrackSample:
        return self.samples[0]

    def predicted_centroid(self, target_frame_index: int | None = None) -> tuple[float, float]:
        cx, cy = self.last_sample.detection.centroid
        vx, vy = self.velocity_frame
        if target_frame_index is None:
            steps = self.missed_frames + 1
        else:
            steps = max(1, int(target_frame_index) - self.last_sample.frame_index)
        return cx + vx * steps, cy + vy * steps

    def append(
        self,
        sample: TrackSample,
        flow: tuple[float, float],
        line_a_s: float,
        line_b_s: float,
        count_line_s: float,
    ) -> None:
        if self.samples:
            previous = self.samples[-1]
            px, py = previous.detection.centroid
            cx, cy = sample.detection.centroid
            frame_delta = max(1, sample.frame_index - previous.frame_index)
            instant = ((cx - px) / frame_delta, (cy - py) / frame_delta)
            self.velocity_frame = (
                self.velocity_frame[0] * 0.45 + instant[0] * 0.55,
                self.velocity_frame[1] * 0.45 + instant[1] * 0.55,
            )
            prev_s = px * flow[0] + py * flow[1]
            curr_s = cx * flow[0] + cy * flow[1]
            self.crossing_a_time = self._crossing_time(
                previous.time_s, sample.time_s, prev_s, curr_s, line_a_s, self.crossing_a_time
            )
            self.crossing_b_time = self._crossing_time(
                previous.time_s, sample.time_s, prev_s, curr_s, line_b_s, self.crossing_b_time
            )
            self.count_crossing_time = self._crossing_time(
                previous.time_s, sample.time_s, prev_s, curr_s, count_line_s, self.count_crossing_time
            )
        self.samples.append(sample)
        if sample.detection.line_a_intersects:
            if self.first_a_intersection is None:
                self.first_a_intersection = sample.time_s
            self.last_a_intersection = sample.time_s
        self.missed_frames = 0

    @staticmethod
    def _crossing_time(
        t0: float,
        t1: float,
        s0: float,
        s1: float,
        line: float,
        existing: float | None,
    ) -> float | None:
        if existing is not None or s1 <= s0:
            return existing
        if not (s0 <= line <= s1):
            return existing
        fraction = (line - s0) / max(1e-12, s1 - s0)
        return t0 + (t1 - t0) * fraction


class DropletTracker:
    def __init__(
        self,
        flow_direction: tuple[float, float],
        line_a_s: float,
        line_b_s: float,
        count_line_s: float,
        fps: float,
        max_distance_px: float,
        max_missed_frames: int = 4,
    ) -> None:
        self.flow = flow_direction
        self.line_a_s = float(line_a_s)
        self.line_b_s = float(line_b_s)
        self.count_line_s = float(count_line_s)
        self.fps = float(fps)
        self.max_distance_px = float(max_distance_px)
        self.max_missed_frames = int(max_missed_frames)
        self._next_id = 1
        self.active: dict[int, Track] = {}
        self.finished: list[Track] = []

    def update(self, frame_index: int, detections: list[Detection]) -> list[tuple[int, Detection]]:
        time_s = frame_index / self.fps
        assignments: list[tuple[float, int, int]] = []
        tx, ty = -self.flow[1], self.flow[0]
        for track_id, track in self.active.items():
            predicted = track.predicted_centroid(frame_index)
            previous = track.last_sample.detection
            previous_area = max(1.0, previous.area_px2)
            previous_length = max(1.0, float(previous.length_px or 1.0))
            previous_width = max(1.0, float(previous.width_px or 1.0))
            for detection_index, detection in enumerate(detections):
                dx = detection.centroid[0] - predicted[0]
                dy = detection.centroid[1] - predicted[1]
                distance = math.hypot(dx, dy)
                if distance > self.max_distance_px:
                    continue
                area_ratio = detection.area_px2 / previous_area
                if area_ratio < 0.20 or area_ratio > 5.0:
                    continue

                last = previous.centroid
                forward = (detection.centroid[0] - last[0]) * self.flow[0] + (
                    detection.centroid[1] - last[1]
                ) * self.flow[1]
                transverse_error = abs(dx * tx + dy * ty)
                backward_penalty = max(0.0, -forward) * 4.0

                # Shape continuity is weak evidence frame-to-frame, but it is
                # useful for separating two close droplets in the same channel.
                shape_penalty = 0.0
                if detection.length_px and previous.length_px:
                    ratio = max(1e-6, float(detection.length_px) / previous_length)
                    shape_penalty += abs(math.log(ratio)) * 8.0
                if detection.width_px and previous.width_px:
                    ratio = max(1e-6, float(detection.width_px) / previous_width)
                    shape_penalty += abs(math.log(ratio)) * 6.0

                artifact_penalty = max(0.0, detection.artifact_overlap - 0.25) * 18.0
                cost = (
                    distance
                    + transverse_error * 0.85
                    + backward_penalty
                    + shape_penalty
                    + artifact_penalty
                )
                assignments.append((cost, track_id, detection_index))

        assignments.sort(key=lambda item: item[0])
        used_tracks: set[int] = set()
        used_detections: set[int] = set()
        matched: list[tuple[int, Detection]] = []
        for _, track_id, detection_index in assignments:
            if track_id in used_tracks or detection_index in used_detections:
                continue
            track = self.active[track_id]
            detection = detections[detection_index]
            track.append(
                TrackSample(frame_index, time_s, detection),
                self.flow,
                self.line_a_s,
                self.line_b_s,
                self.count_line_s,
            )
            used_tracks.add(track_id)
            used_detections.add(detection_index)
            matched.append((track_id, detection))

        for track_id in list(self.active):
            if track_id in used_tracks:
                continue
            track = self.active[track_id]
            track.missed_frames += 1
            if track.missed_frames > self.max_missed_frames:
                self.finished.append(track)
                del self.active[track_id]

        for detection_index, detection in enumerate(detections):
            if detection_index in used_detections:
                continue
            track = Track(self._next_id)
            self._next_id += 1
            track.append(
                TrackSample(frame_index, time_s, detection),
                self.flow,
                self.line_a_s,
                self.line_b_s,
                self.count_line_s,
            )
            self.active[track.track_id] = track
            matched.append((track.track_id, detection))
        return matched

    def finalize(self) -> list[Track]:
        self.finished.extend(self.active.values())
        self.active = {}
        return list(self.finished)


def _project(point: tuple[float, float], axis: tuple[float, float]) -> float:
    return point[0] * axis[0] + point[1] * axis[1]


def _track_geometry_signature(track: Track) -> tuple[float | None, float | None]:
    usable = [
        sample.detection
        for sample in track.samples
        if sample.detection.length_px is not None
        and sample.detection.width_px is not None
        and not sample.detection.touches_roi_edge
    ]
    if not usable:
        return None, None
    lengths = np.asarray([float(d.length_px) for d in usable], dtype=float)
    widths = np.asarray([float(d.width_px) for d in usable], dtype=float)
    return float(np.median(lengths)), float(np.median(widths))



def suppress_duplicate_tracks(
    tracks: list[Track],
    flow: tuple[float, float],
) -> tuple[list[Track], dict[int, int]]:
    """Remove simultaneous inner/outer contour tracks of the same droplet.

    Transparent droplets can produce a stable outer envelope plus one or more
    internal reflection tracks at the same time.  Frame-level NMS cannot always
    remove them because their boxes may not overlap enough.  V67 therefore
    compares complete trajectories: if two tracks coexist for much of the
    shorter track and their centroids remain within one droplet envelope, only
    the stronger/larger geometry track is kept.
    """

    if len(tracks) < 2:
        return tracks, {track.track_id: track.track_id for track in tracks}
    working = list(tracks)
    id_map = {track.track_id: track.track_id for track in working}
    removed: set[int] = set()

    def signature(track: Track):
        lengths = [float(s.detection.length_px) for s in track.samples if s.detection.length_px]
        widths = [float(s.detection.width_px) for s in track.samples if s.detection.width_px]
        areas = [float(s.detection.area_px2) for s in track.samples]
        length = float(np.median(lengths)) if lengths else 20.0
        width = float(np.median(widths)) if widths else 10.0
        area = float(np.median(areas)) if areas else 1.0
        refined = float(np.mean([bool(s.detection.refined_geometry) for s in track.samples])) if track.samples else 0.0
        confidence = track_confidence(track, flow)
        strength = area * (0.75 + 0.35 * refined) * (0.75 + 0.35 * confidence)
        return length, width, area, strength

    signatures = {track.track_id: signature(track) for track in working}
    for i, first in enumerate(working):
        if first.track_id in removed:
            continue
        first_frames = {s.frame_index: s for s in first.samples}
        for second in working[i + 1:]:
            if second.track_id in removed:
                continue
            second_frames = {s.frame_index: s for s in second.samples}
            common_frames = sorted(set(first_frames).intersection(second_frames))
            minimum_common = 2 if min(len(first.samples), len(second.samples)) <= 5 else 3
            if len(common_frames) < minimum_common:
                continue
            overlap_ratio = len(common_frames) / max(1, min(len(first.samples), len(second.samples)))
            if overlap_ratio < 0.42:
                continue

            distances = []
            longitudinal = []
            for frame in common_frames:
                a = first_frames[frame].detection.centroid
                b = second_frames[frame].detection.centroid
                dx, dy = a[0] - b[0], a[1] - b[1]
                distances.append(math.hypot(dx, dy))
                longitudinal.append(abs(dx * flow[0] + dy * flow[1]))
            median_distance = float(np.median(distances))
            median_longitudinal = float(np.median(longitudinal))
            l1, w1, _, strength1 = signatures[first.track_id]
            l2, w2, _, strength2 = signatures[second.track_id]
            envelope = max(l1, l2)
            transverse_scale = max(w1, w2)
            # Adjacent droplets normally remain more than one envelope apart;
            # inner/outer duplicate tracks stay close throughout their overlap.
            if median_distance > max(14.0, min(envelope * 0.48, transverse_scale * 2.4 + 18.0)):
                continue
            if median_longitudinal > max(12.0, envelope * 0.42):
                continue

            if strength1 >= strength2:
                winner, loser = first, second
            else:
                winner, loser = second, first
            removed.add(loser.track_id)
            id_map[loser.track_id] = winner.track_id
            if loser is first:
                break

    kept = [track for track in working if track.track_id not in removed]
    for old in list(id_map):
        mapped = id_map[old]
        while id_map.get(mapped, mapped) != mapped:
            mapped = id_map[mapped]
        id_map[old] = mapped
    return kept, id_map

def stitch_tracks(
    tracks: list[Track],
    flow: tuple[float, float],
    fps: float,
    *,
    max_gap_s: float = 0.45,
) -> tuple[list[Track], dict[int, int]]:
    """Join short fragments that are physically consistent with one trajectory.

    V67 performs this offline, after all frames are known.  Candidate fragments
    are restricted to a short future time window and merged in non-conflicting
    batches.  The batched implementation avoids the cubic behaviour of the
    original one-merge-per-pass version on long recordings with many fragments.
    """

    if len(tracks) < 2:
        return tracks, {track.track_id: track.track_id for track in tracks}

    tx, ty = -flow[1], flow[0]
    working = sorted(tracks, key=lambda t: (t.first_sample.frame_index, t.track_id))
    id_map = {track.track_id: track.track_id for track in working}
    max_gap_frames = max(1, int(round(max_gap_s * max(1.0, fps))))

    # A short fragment can require more than one merge (A->B, then AB->C), but
    # each pass now merges every non-conflicting pair rather than only one pair.
    for _ in range(12):
        if len(working) < 2:
            break

        signatures = {track.track_id: _track_geometry_signature(track) for track in working}
        endpoint_velocity: dict[int, float] = {}
        for track in working:
            if len(track.samples) < 2:
                endpoint_velocity[track.track_id] = 0.0
                continue
            first = track.first_sample
            last = track.last_sample
            dt = max(1e-9, last.time_s - first.time_s)
            ds = _project(last.detection.centroid, flow) - _project(first.detection.centroid, flow)
            endpoint_velocity[track.track_id] = max(0.0, float(ds / dt))

        candidates: list[tuple[float, int, int]] = []
        for i, left in enumerate(working):
            if not left.samples:
                continue
            left_len, left_width = signatures[left.track_id]
            end_sample = left.last_sample
            end_s = _project(end_sample.detection.centroid, flow)
            end_t = _project(end_sample.detection.centroid, (tx, ty))
            velocity = endpoint_velocity[left.track_id]

            for j in range(i + 1, len(working)):
                right = working[j]
                gap_frames = right.first_sample.frame_index - end_sample.frame_index
                if gap_frames <= 0:
                    continue
                if gap_frames > max_gap_frames:
                    # ``working`` is ordered by first frame, so later tracks can
                    # only be farther away in time.
                    break

                start_sample = right.first_sample
                start_s = _project(start_sample.detection.centroid, flow)
                start_t = _project(start_sample.detection.centroid, (tx, ty))
                forward = start_s - end_s
                if forward < -5.0:
                    continue

                dt = max(1e-9, start_sample.time_s - end_sample.time_s)
                predicted_forward = velocity * dt
                longitudinal_error = abs(forward - predicted_forward)

                right_len, right_width = signatures[right.track_id]
                widths = [v for v in (left_width, right_width) if v is not None]
                reference_width = float(np.median(np.asarray(widths, dtype=float))) if widths else 20.0
                max_transverse = max(10.0, reference_width * 0.70)
                transverse_error = abs(start_t - end_t)
                if transverse_error > max_transverse:
                    continue

                max_longitudinal_error = max(
                    14.0,
                    reference_width * 1.25,
                    predicted_forward * 0.65 + 8.0,
                )
                if longitudinal_error > max_longitudinal_error:
                    continue

                shape_cost = 0.0
                if left_len and right_len:
                    ratio = right_len / left_len
                    if ratio < 0.52 or ratio > 1.90:
                        continue
                    shape_cost += abs(math.log(ratio))
                if left_width and right_width:
                    ratio = right_width / left_width
                    if ratio < 0.55 or ratio > 1.80:
                        continue
                    shape_cost += abs(math.log(ratio))

                cost = (
                    longitudinal_error / max_longitudinal_error
                    + transverse_error / max_transverse
                    + gap_frames / max_gap_frames
                    + shape_cost * 0.55
                )
                candidates.append((float(cost), i, j))

        if not candidates:
            break
        candidates.sort(key=lambda item: item[0])
        used: set[int] = set()
        merges: list[tuple[int, int]] = []
        for _, i, j in candidates:
            if i in used or j in used:
                continue
            used.add(i)
            used.add(j)
            merges.append((i, j))
        if not merges:
            break

        remove_indices: set[int] = set()
        for i, j in merges:
            left, right = working[i], working[j]
            canonical_id = left.track_id
            right_ids = {right.track_id}
            right_ids.update(old for old, mapped in id_map.items() if mapped == right.track_id)
            left.samples = sorted(left.samples + right.samples, key=lambda sample: sample.frame_index)
            left.missed_frames = 0
            if len(left.samples) >= 2:
                a, b = left.samples[-2], left.samples[-1]
                dt_frames = max(1, b.frame_index - a.frame_index)
                left.velocity_frame = (
                    (b.detection.centroid[0] - a.detection.centroid[0]) / dt_frames,
                    (b.detection.centroid[1] - a.detection.centroid[1]) / dt_frames,
                )
            for old_id in right_ids:
                id_map[old_id] = canonical_id
            remove_indices.add(j)

        working = [track for index, track in enumerate(working) if index not in remove_indices]
        working.sort(key=lambda t: (t.first_sample.frame_index, t.track_id))

    # Resolve transitive aliases.
    for old in list(id_map):
        mapped = id_map[old]
        while id_map.get(mapped, mapped) != mapped:
            mapped = id_map[mapped]
        id_map[old] = mapped
    return working, id_map

def recompute_track_events(
    track: Track,
    flow: tuple[float, float],
    line_a_s: float,
    line_b_s: float,
    count_line_s: float,
) -> None:
    """Recompute crossings after the final measurement zones are selected."""

    track.crossing_a_time = None
    track.crossing_b_time = None
    track.count_crossing_time = None
    track.first_a_intersection = None
    track.last_a_intersection = None
    count_intersections: list[float] = []

    for sample in track.samples:
        detection = sample.detection
        if detection.min_s is not None and detection.max_s is not None:
            if detection.min_s <= line_a_s <= detection.max_s:
                if track.first_a_intersection is None:
                    track.first_a_intersection = sample.time_s
                track.last_a_intersection = sample.time_s
            if detection.min_s <= count_line_s <= detection.max_s:
                count_intersections.append(sample.time_s)

    for previous, current in zip(track.samples, track.samples[1:]):
        s0 = _project(previous.detection.centroid, flow)
        s1 = _project(current.detection.centroid, flow)
        track.crossing_a_time = Track._crossing_time(
            previous.time_s, current.time_s, s0, s1, line_a_s, track.crossing_a_time
        )
        track.crossing_b_time = Track._crossing_time(
            previous.time_s, current.time_s, s0, s1, line_b_s, track.crossing_b_time
        )
        track.count_crossing_time = Track._crossing_time(
            previous.time_s, current.time_s, s0, s1, count_line_s, track.count_crossing_time
        )

    # If a dropped association skips the centroid crossing but the segmented
    # envelope itself visibly occupies the count line, use that temporal
    # evidence rather than losing the droplet entirely.
    if track.count_crossing_time is None and count_intersections:
        track.count_crossing_time = float(np.median(np.asarray(count_intersections, dtype=float)))


def _robust_velocity_px_s(track: Track, flow: tuple[float, float]) -> float | None:
    if len(track.samples) < 2:
        return None
    velocities: list[float] = []
    for previous, current in zip(track.samples, track.samples[1:]):
        dt = float(current.time_s - previous.time_s)
        if dt <= 1e-9:
            continue
        ds = _project(current.detection.centroid, flow) - _project(previous.detection.centroid, flow)
        if ds <= 0:
            continue
        velocities.append(float(ds / dt))
    if not velocities:
        return None
    values = np.asarray(velocities, dtype=float)
    median = float(np.median(values))
    if len(values) >= 4:
        mad = float(np.median(np.abs(values - median)))
        if mad > 1e-9:
            sigma = 1.4826 * mad
            values = values[np.abs(values - median) <= max(3.5 * sigma, median * 0.45)]
            if len(values):
                median = float(np.median(values))
    return median if math.isfinite(median) and median > 0 else None


def _linear_velocity_and_r2(track: Track, flow: tuple[float, float]) -> tuple[float | None, float]:
    if len(track.samples) < 2:
        return None, 0.0
    times = np.asarray([sample.time_s for sample in track.samples], dtype=np.float64)
    positions = np.asarray([_project(sample.detection.centroid, flow) for sample in track.samples], dtype=np.float64)
    if float(times[-1] - times[0]) <= 1e-9:
        return None, 0.0
    slope, intercept = np.polyfit(times, positions, 1)
    predicted = slope * times + intercept
    residual = float(np.sum((positions - predicted) ** 2))
    total = float(np.sum((positions - np.mean(positions)) ** 2))
    r2 = 1.0 if total <= 1e-9 else max(0.0, min(1.0, 1.0 - residual / total))
    value = float(slope)
    if not math.isfinite(value) or value <= 0:
        return None, r2
    return value, r2


def _linear_velocity_px_s(track: Track, flow: tuple[float, float]) -> float | None:
    return _linear_velocity_and_r2(track, flow)[0]


def _robust_relative_variation(values: np.ndarray) -> float:
    if values.size < 2:
        return 0.0
    median = float(np.median(values))
    if abs(median) <= 1e-9:
        return 1.0
    mad = float(np.median(np.abs(values - median)))
    return max(0.0, min(2.0, 1.4826 * mad / abs(median)))


def _stable_geometry_samples(
    samples: list[TrackSample],
    flow: tuple[float, float],
    count_line_s: float | None,
    reference_length_px: float | None = None,
) -> tuple[list[TrackSample], float | None]:
    """Return conservative geometry samples for one physical track.

    V67 became too eager to promote the largest visible contour to the outer
    envelope.  That fixed a few internal-reflection cases but made clean videos
    less stable and could amplify glare.  V3.2 restores the V66 principle: the
    track's robust local median is authoritative unless a recording-level
    reference provides strong evidence that the median is a partial/internal
    contour.  The reference is only a repair signal, never a target that all
    droplets are forced to match.
    """

    good = [
        sample
        for sample in samples
        if not sample.detection.touches_roi_edge
        and sample.detection.length_px is not None
        and sample.detection.width_px is not None
        and sample.detection.artifact_overlap <= 0.55
    ]
    refined = [sample for sample in good if sample.detection.refined_geometry]
    if len(refined) >= 2:
        good = refined
    if not good:
        return [], None

    # Measure near the count event when possible.  This keeps entry/exit glare
    # and partially visible contours out of an otherwise good track.
    if count_line_s is not None and len(good) >= 4:
        median_length = float(np.median([float(s.detection.length_px) for s in good]))
        half_window = max(52.0, median_length * 1.45)
        local = [
            sample for sample in good
            if abs(_project(sample.detection.centroid, flow) - count_line_s) <= half_window
        ]
        if len(local) >= 3:
            good = local

    if len(good) < 3:
        variation = _robust_relative_variation(
            np.asarray([float(s.detection.length_px) for s in good], dtype=float)
        ) if good else None
        return good, variation

    lengths = np.asarray([float(sample.detection.length_px) for sample in good], dtype=float)
    widths = np.asarray([float(sample.detection.width_px) for sample in good], dtype=float)
    areas = np.asarray([float(sample.detection.area_px2) for sample in good], dtype=float)

    # Reference-guided repair is deliberately narrow.  It is only used when a
    # track is clearly shorter/longer than the stable recording mode and at
    # least two observations independently support geometry near that mode.
    reference = float(reference_length_px) if reference_length_px else None
    if reference is not None and reference > 1.0 and len(good) >= 4:
        baseline = float(np.median(lengths))
        baseline_width = float(np.median(widths))
        suspicious_low = baseline < reference * 0.68
        suspicious_high = baseline > reference * 1.55
        if suspicious_low or suspicious_high:
            near_reference = (
                (lengths >= reference * 0.76)
                & (lengths <= reference * 1.34)
            )
            candidates = [sample for sample, keep in zip(good, near_reference) if bool(keep)]
            if len(candidates) >= 2:
                candidate_lengths = np.asarray(
                    [float(s.detection.length_px) for s in candidates], dtype=float
                )
                candidate_widths = np.asarray(
                    [float(s.detection.width_px) for s in candidates], dtype=float
                )
                candidate_variation = _robust_relative_variation(candidate_lengths)
                width_support = (
                    suspicious_high
                    or float(np.median(candidate_widths)) >= baseline_width * 1.18
                )
                # For an internal-reflection repair we require a wider envelope;
                # for an oversized-glare repair, closeness to the reference is
                # sufficient because the replacement is smaller, not larger.
                if candidate_variation <= 0.20 and width_support:
                    good = candidates
                    lengths = candidate_lengths
                    widths = candidate_widths
                    areas = np.asarray(
                        [float(s.detection.area_px2) for s in candidates], dtype=float
                    )

    def robust_keep(values: np.ndarray, relative_floor: float) -> np.ndarray:
        median = float(np.median(values))
        mad = float(np.median(np.abs(values - median)))
        if mad <= 1e-9:
            return np.abs(values - median) <= max(1.0, abs(median) * relative_floor)
        sigma = 1.4826 * mad
        return np.abs(values - median) <= max(3.0 * sigma, abs(median) * relative_floor)

    keep = robust_keep(lengths, 0.18)
    keep &= robust_keep(widths, 0.22)
    keep &= robust_keep(areas, 0.32)
    stable = [sample for sample, use in zip(good, keep) if bool(use)]
    if len(stable) < 2:
        stable = good
    variation = _robust_relative_variation(
        np.asarray([float(s.detection.length_px) for s in stable], dtype=float)
    )
    return stable, variation


def track_confidence(track: Track, flow: tuple[float, float]) -> float:
    if not track.samples:
        return 0.0
    sample_score = min(1.0, len(track.samples) / 7.0)

    positions = np.asarray([_project(s.detection.centroid, flow) for s in track.samples], dtype=float)
    if len(positions) >= 2:
        deltas = np.diff(positions)
        monotonic = float(np.mean(deltas >= -1.5))
    else:
        monotonic = 0.0

    lengths = np.asarray(
        [float(s.detection.length_px) for s in track.samples if s.detection.length_px], dtype=float
    )
    widths = np.asarray(
        [float(s.detection.width_px) for s in track.samples if s.detection.width_px], dtype=float
    )
    variation = max(_robust_relative_variation(lengths), _robust_relative_variation(widths))
    geometry_score = max(0.0, min(1.0, 1.0 - variation / 0.45))
    refined_fraction = float(np.mean([bool(s.detection.refined_geometry) for s in track.samples]))
    edge_fraction = float(np.mean([bool(s.detection.touches_roi_edge) for s in track.samples]))
    artifact = float(np.mean([float(s.detection.artifact_overlap) for s in track.samples]))
    _, trajectory_r2 = _linear_velocity_and_r2(track, flow)

    score = (
        0.16 * sample_score
        + 0.19 * monotonic
        + 0.18 * geometry_score
        + 0.13 * refined_fraction
        + 0.12 * (1.0 - edge_fraction)
        + 0.10 * (1.0 - min(1.0, artifact))
        + 0.12 * trajectory_r2
    )
    return float(max(0.0, min(1.0, score)))


def track_to_measurement(
    track: Track,
    flow: tuple[float, float],
    fps: float,
    line_distance_px: float,
    calibration: Calibration,
    count_line_s: float | None = None,
    *,
    geometry_tracks: list[Track] | None = None,
    event_time_s: float | None = None,
    reference_length_px: float | None = None,
) -> DropletMeasurement | None:
    """Convert one counted event into a robust measurement.

    ``track`` remains authoritative for timing and velocity.  ``geometry_tracks``
    may contain one or more co-temporal outer-envelope tracks selected by the
    offline event-fusion stage.  This separation is important for transparent
    droplets: a short internal reflection can be the cleanest trajectory across
    the count line while a simultaneous, larger track contains the physically
    correct outer boundary.
    """

    if track.count_crossing_time is None and event_time_s is None:
        return None
    event_time = float(track.count_crossing_time if event_time_s is None else event_time_s)

    geometry_sources = list(geometry_tracks or [track])
    # Keep one entry per physical track and always retain the primary track as a
    # fallback if an event-fusion caller supplies an empty/invalid list.
    unique_sources: list[Track] = []
    seen_ids: set[int] = set()
    for source in geometry_sources:
        if source.track_id in seen_ids:
            continue
        seen_ids.add(source.track_id)
        unique_sources.append(source)
    if not unique_sources:
        unique_sources = [track]

    per_track_geometry: list[tuple[Track, list[TrackSample], float | None]] = []
    event_half_window = max(0.28, 3.0 / max(1.0, fps))
    for source in unique_sources:
        local = [sample for sample in source.samples if abs(sample.time_s - event_time) <= event_half_window]
        # The outer envelope may be fragmented exactly at the count line.  If
        # there are too few local samples, let the existing spatial measurement
        # window choose stable samples from the complete geometry track.
        candidate_samples = local if len(local) >= 2 else source.samples
        good, variation = _stable_geometry_samples(
            candidate_samples, flow, count_line_s, reference_length_px
        )
        if good:
            per_track_geometry.append((source, good, variation))

    good_geometry: list[TrackSample] = []
    geometry_variation = None
    if per_track_geometry:
        # Event fusion already rejects short internal tracks.  Aggregate each
        # surviving outer-envelope source as one vote rather than allowing a
        # long fragment with many samples to dominate a shorter compatible
        # fragment.
        length_votes: list[float] = []
        width_votes: list[float] = []
        area_votes: list[float] = []
        variations: list[float] = []
        for _, samples, variation in per_track_geometry:
            length_votes.append(float(np.median([float(s.detection.length_px) for s in samples])))
            width_votes.append(float(np.median([float(s.detection.width_px) for s in samples])))
            area_votes.append(float(np.median([float(s.detection.area_px2) for s in samples])))
            if variation is not None:
                variations.append(float(variation))
            good_geometry.extend(samples)
        fused_length_px = float(np.median(np.asarray(length_votes, dtype=float)))
        fused_width_px = float(np.median(np.asarray(width_votes, dtype=float)))
        fused_area_px2 = float(np.median(np.asarray(area_votes, dtype=float)))
        cross_variation = _robust_relative_variation(np.asarray(length_votes, dtype=float))
        geometry_variation = max(
            cross_variation,
            float(np.median(np.asarray(variations, dtype=float))) if variations else 0.0,
        )
    else:
        fused_length_px = None
        fused_width_px = None
        fused_area_px2 = None
    transit_velocity = None
    if (
        track.crossing_a_time is not None
        and track.crossing_b_time is not None
        and track.crossing_b_time > track.crossing_a_time
        and line_distance_px > 0
    ):
        transit_velocity = abs(line_distance_px / (track.crossing_b_time - track.crossing_a_time))

    robust_velocity = _robust_velocity_px_s(track, flow)
    linear_velocity, linear_r2 = _linear_velocity_and_r2(track, flow)
    candidates = [v for v in (transit_velocity, robust_velocity, linear_velocity) if v is not None and math.isfinite(v)]
    velocity = None
    if candidates:
        # When estimates broadly agree, their median is very stable.  If the
        # A/B estimate is an outlier from a temporary contour jump, regression
        # and per-step motion dominate automatically.
        values = np.asarray(candidates, dtype=float)
        median = float(np.median(values))
        close = values[np.abs(values - median) <= max(0.35 * median, 1.0)]
        velocity = float(np.median(close if len(close) else values))
        if linear_velocity is not None and linear_r2 >= 0.90 and (
            velocity <= 0 or abs(linear_velocity - velocity) / max(1.0, velocity) > 0.55
        ):
            velocity = linear_velocity

    method = "geometry"
    rejection_reason = ""
    length_px: float | None = None
    width_px: float | None = None
    area_px2: float | None = None
    representative = min(track.samples, key=lambda item: abs(item.time_s - event_time))

    if good_geometry and fused_length_px is not None:
        length_px = fused_length_px
        width_px = fused_width_px
        area_px2 = fused_area_px2
        representative = min(good_geometry, key=lambda item: abs(item.time_s - event_time))
    elif (
        velocity is not None
        and track.first_a_intersection is not None
        and track.last_a_intersection is not None
    ):
        occupancy = max(1.0 / fps, track.last_a_intersection - track.first_a_intersection + 1.0 / fps)
        length_px = float(velocity * occupancy)
        method = "transit"
        area_px2 = float(np.median([sample.detection.area_px2 for sample in track.samples]))
    else:
        rejection_reason = "No complete geometry or usable transit measurement."

    primary_confidence = track_confidence(track, flow)
    geometry_confidences = [track_confidence(source, flow) for source, _, _ in per_track_geometry]
    geometry_confidence = float(np.median(geometry_confidences)) if geometry_confidences else primary_confidence
    # A temporal event plus a strong outer-envelope source can legitimately
    # rescue a count-line track that is short because the internal reflection
    # was easier to follow.  Do not make the weak timing fragment cap the final
    # geometry confidence.
    event_fused = bool(geometry_tracks)
    confidence = float(max(primary_confidence, geometry_confidence * (0.94 if event_fused else 1.0)))
    geometry_observations = len({sample.frame_index for sample in good_geometry})
    required_observations = 2 if event_fused else 3
    observation_support = (
        geometry_observations >= required_observations
        if method == "geometry"
        else len(track.samples) >= 3
    )
    valid = bool(
        length_px is not None
        and length_px > 0
        and observation_support
        and confidence >= 0.42
    )
    if not valid and not rejection_reason:
        if confidence < 0.42:
            rejection_reason = "Low track confidence (unstable shape or trajectory)."
        else:
            rejection_reason = "Insufficient stable observations."
    aspect_ratio = None
    if length_px and width_px and width_px > 1e-9:
        aspect_ratio = float(length_px / width_px)

    measurement = DropletMeasurement(
        droplet_id=track.track_id,
        timestamp_s=event_time,
        frame_index=int(representative.frame_index),
        length_px=length_px,
        width_px=width_px,
        area_px2=area_px2,
        aspect_ratio=aspect_ratio,
        velocity_px_s=velocity,
        spacing_px=None,
        method=method,
        valid=valid,
        rejection_reason=rejection_reason,
        counted=True,
        confidence_score=confidence,
        observation_count=max(len(track.samples), geometry_observations),
        geometry_variation=geometry_variation,
    )
    measurement.apply_calibration(calibration)
    return measurement


def overlay_for_detection(track_id: int, detection: Detection, flow: tuple[float, float]) -> OverlayDetection:
    contour = detection.contour.reshape(-1, 2)
    if len(contour) > 24:
        epsilon = max(1.0, 0.006 * cv_arc_length(contour))
        simplified = approximate_contour(detection.contour, epsilon)
    else:
        simplified = contour
    length_start = length_end = None
    if detection.min_s is not None and detection.max_s is not None:
        cx, cy = detection.centroid
        center_s = cx * flow[0] + cy * flow[1]
        start_offset = detection.min_s - center_s
        end_offset = detection.max_s - center_s
        length_start = (cx + flow[0] * start_offset, cy + flow[1] * start_offset)
        length_end = (cx + flow[0] * end_offset, cy + flow[1] * end_offset)
    return OverlayDetection(
        droplet_id=track_id,
        contour=[(int(x), int(y)) for x, y in simplified.reshape(-1, 2)],
        centroid=detection.centroid,
        length_start=length_start,
        length_end=length_end,
        valid_candidate=(not detection.touches_roi_edge and detection.artifact_overlap < 0.60),
    )


def cv_arc_length(contour: np.ndarray) -> float:
    import cv2
    return float(cv2.arcLength(contour.astype(np.float32), True))


def approximate_contour(contour: np.ndarray, epsilon: float) -> np.ndarray:
    import cv2
    return cv2.approxPolyDP(contour.astype(np.float32), epsilon, True).astype(np.int32)
