"""Lightweight temporal crossing validation for offline droplet analysis.

This is not a second full detector.  It samples a thin band around the final
count line and looks for coherent changes against the temporal background.  The
signal acts as an independent check on tracker crossing events and can recover a
crossing timestamp for a track that was briefly fragmented exactly at the line.
"""

from __future__ import annotations

from dataclasses import dataclass
import math

import cv2
import numpy as np

from .video_source import VideoSource


@dataclass(frozen=True)
class TemporalCrossingResult:
    event_times_s: tuple[float, ...]
    threshold: float
    baseline: float
    noise_sigma: float


def _line_band_mask(
    roi_px: tuple[int, int, int, int],
    flow: tuple[float, float],
    line_s: float,
    *,
    half_width_px: float = 4.0,
) -> tuple[np.ndarray, tuple[int, int, int, int]]:
    x0, y0, w, h = roi_px
    yy, xx = np.indices((h, w), dtype=np.float32)
    global_x = xx + float(x0)
    global_y = yy + float(y0)
    s = global_x * float(flow[0]) + global_y * float(flow[1])
    mask = np.abs(s - float(line_s)) <= float(half_width_px)
    ys, xs = np.nonzero(mask)
    if len(xs) == 0:
        return np.zeros((1, 1), dtype=bool), (x0, y0, 1, 1)
    xa, xb = int(xs.min()), int(xs.max()) + 1
    ya, yb = int(ys.min()), int(ys.max()) + 1
    return mask[ya:yb, xa:xb], (x0 + xa, y0 + ya, xb - xa, yb - ya)


def crossing_events(
    source: VideoSource,
    *,
    roi_px: tuple[int, int, int, int],
    background_gray: np.ndarray,
    artifact_mask: np.ndarray | None,
    flow: tuple[float, float],
    line_s: float,
) -> TemporalCrossingResult:
    """Return independent count-line event times from a thin temporal signal."""

    band_mask, bbox = _line_band_mask(roi_px, flow, line_s)
    gx, gy, gw, gh = bbox
    rx, ry, _, _ = roi_px
    local_x = gx - rx
    local_y = gy - ry
    bg_crop = background_gray[local_y:local_y + gh, local_x:local_x + gw]
    valid = band_mask.copy()
    if artifact_mask is not None and artifact_mask.size:
        art = artifact_mask[local_y:local_y + gh, local_x:local_x + gw] > 0
        valid &= ~art
    if np.count_nonzero(valid) < 8:
        valid = band_mask

    scores: list[float] = []
    times: list[float] = []
    fps = max(1e-6, float(source.metadata.fps))
    for frame_index, frame in source.iter_frames():
        crop = frame[gy:gy + gh, gx:gx + gw]
        if crop.size == 0 or crop.shape[:2] != bg_crop.shape[:2]:
            continue
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        diff = cv2.absdiff(gray, bg_crop)
        values = diff[valid]
        if values.size == 0:
            continue
        # The upper quartile reacts to a droplet occupying part of the line but
        # is much less sensitive to one isolated hot pixel than max().
        cutoff = float(np.percentile(values, 75.0))
        top = values[values >= cutoff]
        score = float(np.mean(top)) if top.size else float(np.mean(values))
        scores.append(score)
        times.append(frame_index / fps)

    if len(scores) < 3:
        return TemporalCrossingResult((), 0.0, 0.0, 0.0)

    values = np.asarray(scores, dtype=np.float64)
    # Three-sample smoothing removes frame-level sparkle without shifting event
    # timing appreciably at the 10-30 fps microscope rates.
    if len(values) >= 3:
        values = np.convolve(values, np.asarray([0.25, 0.50, 0.25]), mode="same")
        values[0] = scores[0]
        values[-1] = scores[-1]

    baseline = float(np.median(values))
    mad = float(np.median(np.abs(values - baseline)))
    sigma = 1.4826 * mad
    q90 = float(np.percentile(values, 90.0))
    threshold = max(baseline + max(3.2 * sigma, 2.5), baseline + (q90 - baseline) * 0.42)

    active = values >= threshold
    events: list[tuple[float, float]] = []  # (time, score)
    start = None
    for i, is_active in enumerate(active):
        if is_active and start is None:
            start = i
        if start is not None and (not is_active or i == len(active) - 1):
            end = i if is_active and i == len(active) - 1 else i - 1
            if end >= start:
                segment = values[start:end + 1]
                peak_rel = int(np.argmax(segment))
                peak_index = start + peak_rel
                events.append((float(times[peak_index]), float(values[peak_index])))
            start = None

    if not events:
        return TemporalCrossingResult((), threshold, baseline, sigma)

    # Collapse repeated peaks from the same physical droplet.  Keep the stronger
    # event if two peaks occur closer than 150 ms.
    min_sep = max(0.15, 1.25 / fps)
    merged: list[tuple[float, float]] = []
    for event in events:
        if not merged or event[0] - merged[-1][0] >= min_sep:
            merged.append(event)
        elif event[1] > merged[-1][1]:
            merged[-1] = event
    return TemporalCrossingResult(tuple(time for time, _ in merged), threshold, baseline, sigma)


def match_event_times(
    track_times: list[float],
    temporal_times: tuple[float, ...],
    *,
    tolerance_s: float,
) -> tuple[int, dict[int, float], list[float]]:
    """Match tracker events to temporal events without reusing either event."""

    candidates: list[tuple[float, int, int]] = []
    for i, track_time in enumerate(track_times):
        for j, temporal_time in enumerate(temporal_times):
            error = abs(float(track_time) - float(temporal_time))
            if error <= tolerance_s:
                candidates.append((error, i, j))
    candidates.sort(key=lambda item: item[0])
    used_tracks: set[int] = set()
    used_temporal: set[int] = set()
    matched: dict[int, float] = {}
    for _, i, j in candidates:
        if i in used_tracks or j in used_temporal:
            continue
        used_tracks.add(i)
        used_temporal.add(j)
        matched[i] = float(temporal_times[j])
    unmatched = [float(t) for j, t in enumerate(temporal_times) if j not in used_temporal]
    return len(matched), matched, unmatched
