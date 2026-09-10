from __future__ import annotations

from backend.analytics.analyzer import DropletAnalyzer
from backend.analytics.models import Calibration, Detection, DropletMeasurement
from backend.analytics.tracker import Track, TrackSample


def _measurement(timestamp: float, *, length: float | None, valid: bool = True):
    return DropletMeasurement(
        droplet_id=1,
        timestamp_s=timestamp,
        frame_index=int(timestamp * 10),
        length_px=length,
        width_px=40.0 if length is not None else None,
        area_px2=2000.0 if length is not None else None,
        aspect_ratio=None,
        velocity_px_s=100.0,
        spacing_px=None,
        method="geometry",
        valid=valid,
        counted=True,
        confidence_score=0.8,
        observation_count=6,
        geometry_variation=0.1,
    )


def _track(track_id: int, center_time: float, crossing: float, length: float = 82.0) -> Track:
    samples = []
    for i, offset in enumerate((-0.6, -0.4, -0.2, 0.0, 0.2, 0.4, 0.6)):
        time_s = center_time + offset
        x = 50.0 + offset * 8.0
        detection = Detection(
            contour=None,
            centroid=(x, 20.0),
            area_px2=length * 35.0,
            bbox=(0, 0, int(length), 35),
            length_px=length,
            width_px=35.0,
            min_s=x - length / 2.0,
            max_s=x + length / 2.0,
            min_t=0.0,
            max_t=35.0,
            refined_geometry=True,
            artifact_overlap=0.0,
        )
        samples.append(TrackSample(frame_index=i, time_s=time_s, detection=detection))
    return Track(track_id=track_id, samples=samples, count_crossing_time=crossing)


def test_stable_reference_rejects_unrecovered_geometry_without_dropping_count():
    measurement = _measurement(1.0, length=180.0)
    result = DropletAnalyzer._apply_event_geometry_repair(
        measurement,
        direct_geometry=None,
        reference_length=85.0,
        calibration=Calibration(),
        stable_reference_length=82.0,
    )
    assert result.counted is True
    assert result.valid is False
    assert result.length_px == 180.0
    assert "size excluded" in result.rejection_reason


def test_multimodal_recording_hint_does_not_reject_without_stable_reference():
    measurement = _measurement(1.0, length=180.0)
    result = DropletAnalyzer._apply_event_geometry_repair(
        measurement,
        direct_geometry=None,
        reference_length=140.0,
        calibration=Calibration(),
        stable_reference_length=None,
    )
    assert result.valid is True


def test_generation_period_uses_counted_events_even_when_geometry_is_invalid():
    measurements = [
        _measurement(0.0, length=80.0, valid=True),
        _measurement(2.0, length=180.0, valid=False),
        _measurement(4.0, length=82.0, valid=True),
    ]
    summary = DropletAnalyzer._build_summary(measurements, (), fps=10.0)
    assert summary.total_droplets == 3
    assert summary.valid_droplets == 2
    assert summary.generation_period_s == 2.0
    assert summary.generation_rate_s == 0.5


def test_temporal_duplicate_peaks_on_same_track_collapse_and_isolated_tracker_is_rejected():
    shared = _track(1, 10.3, crossing=10.2)
    second = _track(2, 15.0, crossing=15.0)
    isolated = _track(3, 50.0, crossing=50.0)
    assignments, tracker_only, _ = DropletAnalyzer._temporal_event_assignments(
        [shared, second, isolated],
        (10.0, 10.6, 15.0),
        count_line_s=50.0,
        flow=(1.0, 0.0),
        fps=10.0,
        typical_length=82.0,
    )
    assert assignments is not None
    assert len(assignments) == 2
    assert [item[1].track_id for item in assignments if item[1] is not None] == [1, 2]
    assert tracker_only == []
