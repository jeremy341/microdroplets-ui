from __future__ import annotations

import numpy as np
import pytest

from backend.analytics.analyzer import DropletAnalyzer
from backend.analytics.models import Calibration, Detection, DropletMeasurement
from backend.analytics.tracker import Track, TrackSample, track_to_measurement


def _sample(frame: int, *, x: float, length: float, width: float, area: float | None = None) -> TrackSample:
    half_l = length / 2.0
    half_w = width / 2.0
    contour = np.asarray(
        [
            [[int(round(x - half_l)), int(round(100 - half_w))]],
            [[int(round(x + half_l)), int(round(100 - half_w))]],
            [[int(round(x + half_l)), int(round(100 + half_w))]],
            [[int(round(x - half_l)), int(round(100 + half_w))]],
        ],
        dtype=np.int32,
    )
    detection = Detection(
        contour=contour,
        centroid=(float(x), 100.0),
        area_px2=float(area if area is not None else length * width * 0.75),
        bbox=(int(x - half_l), int(100 - half_w), max(1, int(length)), max(1, int(width))),
        length_px=float(length),
        width_px=float(width),
        min_s=float(x - half_l),
        max_s=float(x + half_l),
        min_t=float(100 - half_w),
        max_t=float(100 + half_w),
        refined_geometry=True,
        refinement_score=8.0,
        artifact_overlap=0.0,
    )
    return TrackSample(frame_index=frame, time_s=frame / 10.0, detection=detection)


def _track(track_id: int, lengths: list[float], widths: list[float], *, start_frame: int = 40) -> Track:
    track = Track(track_id)
    for offset, (length, width) in enumerate(zip(lengths, widths)):
        frame = start_frame + offset
        x = 86.0 + offset * 6.0
        track.samples.append(_sample(frame, x=x, length=length, width=width))
    track.count_crossing_time = (start_frame + len(lengths) // 2) / 10.0
    return track


def test_event_geometry_prefers_outer_envelope_over_internal_reflection():
    # The internal reflection is easier to track and is therefore the timing
    # track.  A simultaneous wider/larger track represents the real envelope.
    inner = _track(10, [34, 35, 36, 35, 34, 35], [12, 13, 12, 13, 12, 13])
    outer = _track(11, [126, 130, 132, 131, 129, 130], [52, 55, 56, 55, 54, 55])
    event_time = 4.3
    sources = DropletAnalyzer._event_geometry_sources(
        [inner, outer], inner, event_time, 104.0, (1.0, 0.0), 10.0, 130.0
    )
    assert outer in sources
    assert inner not in sources

    measurement = track_to_measurement(
        inner,
        (1.0, 0.0),
        10.0,
        120.0,
        Calibration(),
        104.0,
        geometry_tracks=sources,
        event_time_s=event_time,
    )
    assert measurement is not None
    assert measurement.valid
    assert measurement.length_px == pytest.approx(130.0, abs=4.0)
    assert measurement.width_px == pytest.approx(55.0, abs=4.0)


def test_geometry_consensus_uses_stable_outer_phase_not_transition_median():
    # This mirrors the real 164623 failure: the same track initially contains
    # internal refraction and only later resolves into the complete envelope.
    track = _track(
        20,
        [36.0, 49.8, 87.8, 87.8, 120.6, 130.1, 130.1],
        [13.5, 12.7, 15.8, 15.8, 53.8, 56.3, 56.9],
        start_frame=62,
    )
    measurement = track_to_measurement(
        track,
        (1.0, 0.0),
        10.0,
        120.0,
        Calibration(),
        104.0,
        event_time_s=6.5,
        reference_length_px=130.0,
    )
    assert measurement is not None
    assert measurement.valid
    assert measurement.length_px == pytest.approx(130.1, abs=4.0)
    assert measurement.width_px == pytest.approx(56.0, abs=5.0)


def test_generation_rate_uses_crossing_period_not_full_video_duration():
    measurements = [
        DropletMeasurement(i + 1, t, int(t * 30), 100.0, 30.0, 2500.0, 3.3, 50.0, None, "geometry", True, confidence_score=0.9)
        for i, t in enumerate((2.0, 2.3, 2.6, 2.9))
    ]
    summary = DropletAnalyzer._build_summary(measurements, (), 30.0)
    assert summary.generation_period_s == pytest.approx(0.3, abs=1e-6)
    assert summary.generation_rate_s == pytest.approx(1.0 / 0.3, rel=1e-6)
