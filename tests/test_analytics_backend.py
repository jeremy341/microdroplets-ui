from __future__ import annotations

import csv
import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from backend.analytics import AnalysisConfig, AnalysisResult, DropletAnalyzer
from backend.analytics import analyzer as analyzer_module
from backend.analytics.calibration import manual_scale, scale_from_reference
from backend.analytics.csv_export import CSV_FIELDS, export_result_csv
from backend.analytics.detector import DropletDetector, apply_flow_geometry, build_background
from backend.analytics.models import Calibration, Detection
from backend.analytics.result_store import ResultStore
from backend.analytics.video_source import VideoSource, discover_videos


def _make_synthetic_video(path: Path, *, width=640, height=240, fps=30, frames=180) -> Path:
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), fps, (width, height))
    assert writer.isOpened()
    for frame_index in range(frames):
        frame = np.full((height, width, 3), 205, np.uint8)
        cv2.line(frame, (0, 80), (width, 80), (70, 70, 70), 5)
        cv2.line(frame, (0, 160), (width, 160), (70, 70, 70), 5)
        for offset in (-120, 80, 280, 480):
            x = int(-100 + frame_index * 5 + offset)
            if -60 < x < width + 60:
                cv2.ellipse(frame, (x, 120), (34, 22), 0, 0, 360, (120, 120, 120), -1)
                cv2.ellipse(frame, (x, 120), (34, 22), 0, 0, 360, (60, 60, 60), 2)
        writer.write(frame)
    writer.release()
    return path


def _make_truncated_video(path: Path, *, frames=150, keep=0.55) -> Path:
    """A recording whose header still advertises every frame but whose data
    stops part-way through, so the decoder fails before the reported count."""

    complete = _make_synthetic_video(path, frames=frames)
    payload = complete.read_bytes()
    truncated = path.with_name(path.stem + "_trunc" + path.suffix)
    truncated.write_bytes(payload[: int(len(payload) * keep)])
    complete.unlink()
    return truncated


class _TailUndecodableSource(VideoSource):
    """VideoSource whose sample indices past the midpoint cannot be decoded."""

    def read_frame(self, frame_index: int):
        if frame_index > self.metadata.frame_count // 2:
            raise RuntimeError(f"synthetic decode failure at frame {frame_index}")
        return super().read_frame(frame_index)


class _SparseUndecodableSource(VideoSource):
    """VideoSource whose sample indices past the first tenth cannot be decoded.

    That leaves roughly one usable background sample, all of it from the head of
    the recording: the temporal median is then taken from a one-sided subset.
    """

    def read_frame(self, frame_index: int):
        if frame_index > self.metadata.frame_count // 10:
            raise RuntimeError(f"synthetic decode failure at frame {frame_index}")
        return super().read_frame(frame_index)


class _MarginallyUndecodableSource(VideoSource):
    """VideoSource that loses only the last two sampled frames."""

    def read_frame(self, frame_index: int):
        dropped = getattr(self, "_dropped", 0)
        if dropped < 2 and frame_index > self.metadata.frame_count * 0.9:
            self._dropped = dropped + 1
            raise RuntimeError(f"synthetic decode failure at frame {frame_index}")
        return super().read_frame(frame_index)


class _UndecodableSource(VideoSource):
    """VideoSource whose every random-access read fails."""

    def read_frame(self, frame_index: int):
        raise RuntimeError(f"synthetic decode failure at frame {frame_index}")


class _PartialUndecodableSource(VideoSource):
    """VideoSource whose random-access reads fail outside ``allowed``.

    Used to starve the pre-pass estimation helpers (channel ROI, flow direction,
    event geometry) while leaving the background sample grid readable.
    ``iter_frames`` is untouched either way, so the sequential frame pass still
    runs to the end: only a helper-integrity gate can tell the two apart.
    """

    allowed: frozenset = frozenset()

    def read_frame(self, frame_index: int):
        if int(frame_index) not in self.allowed:
            raise RuntimeError(f"synthetic decode failure at frame {frame_index}")
        return super().read_frame(frame_index)


def _background_grid(frame_count: int, samples: int) -> frozenset:
    """The exact frame grid ``build_background`` samples for this clip."""

    return frozenset(np.linspace(0, frame_count - 1, samples, dtype=int).tolist())


def test_video_source_and_capture_discovery(tmp_path):
    video = _make_synthetic_video(tmp_path / "run.avi", frames=60)
    source = VideoSource(video)
    assert source.metadata.width == 640
    assert source.metadata.height == 240
    assert source.metadata.frame_count == 60
    assert source.metadata.fps == pytest.approx(30, rel=0.05)
    assert source.read_frame(10).shape[:2] == (240, 640)
    assert discover_videos(tmp_path) == [video]


def test_calibration_supports_manual_reference_and_pixels_only():
    manual = manual_scale(1.92, (1280, 1024))
    assert manual.length_um(100) == pytest.approx(192)
    assert manual.velocity_mm_s(1000) == pytest.approx(1.92)
    reference = scale_from_reference(500, 1000, (1280, 1024))
    assert reference.um_per_px == pytest.approx(2.0)
    assert Calibration().length_um(100) is None


def test_projection_geometry_is_rotation_invariant():
    center = (180, 150)
    axes = (50, 20)
    angle = 30
    points = cv2.ellipse2Poly(center, axes, angle, 0, 360, 4).reshape(-1, 1, 2)
    moments = cv2.moments(points)
    detection = Detection(
        contour=points,
        centroid=(moments["m10"] / moments["m00"], moments["m01"] / moments["m00"]),
        area_px2=float(cv2.contourArea(points)),
        bbox=cv2.boundingRect(points),
    )
    radians = np.deg2rad(angle)
    flow = (float(np.cos(radians)), float(np.sin(radians)))
    apply_flow_geometry(detection, flow, 0.0)
    assert detection.length_px == pytest.approx(100, abs=5)
    assert detection.width_px == pytest.approx(40, abs=5)


def test_end_to_end_auto_analysis_counts_unique_droplets_and_measures_length(tmp_path):
    video = _make_synthetic_video(tmp_path / "droplets.avi")
    config = AnalysisConfig(roi=(0, 0.25, 1, 0.5), flow_direction=None, background_samples=21)
    result = DropletAnalyzer(config).analyze(video)
    assert result.complete
    assert result.flow_direction[0] > 0.95
    assert result.summary.total_droplets >= 3
    assert result.summary.valid_droplets == result.summary.total_droplets
    assert result.summary.mean_length_px == pytest.approx(70, abs=5)
    assert result.summary.generation_rate_s and result.summary.generation_rate_s > 0
    assert len(result.overlays) > 20
    # The same physical droplet is observed in many frames but yields one row.
    assert len(result.measurements) < 10
    assert all(item.method in {"geometry", "transit"} for item in result.measurements)


def test_calibrated_analysis_populates_physical_units(tmp_path):
    video = _make_synthetic_video(tmp_path / "calibrated.avi")
    config = AnalysisConfig(
        roi=(0, 0.25, 1, 0.5),
        flow_direction=(1, 0),
        calibration=manual_scale(2.0, (640, 240)),
        background_samples=21,
    )
    result = DropletAnalyzer(config).analyze(video)
    assert result.summary.mean_length_um == pytest.approx(140, abs=10)
    assert all(item.length_um is not None for item in result.measurements if item.valid)
    assert all(item.velocity_mm_s is not None for item in result.measurements if item.valid)


def test_analysis_cancellation_returns_partial_result(tmp_path):
    video = _make_synthetic_video(tmp_path / "cancel.avi")
    stop = {"value": False}

    def progress(done, total):
        if done >= total // 3:
            stop["value"] = True

    result = DropletAnalyzer(AnalysisConfig(roi=(0, 0.25, 1, 0.5), flow_direction=(1, 0))).analyze(
        video,
        progress_callback=progress,
        cancel_check=lambda: stop["value"],
    )
    assert not result.complete
    assert 0 < result.processed_frames < result.metadata.frame_count


def test_incomplete_results_are_never_cached(tmp_path):
    store = ResultStore(tmp_path / "analytics", tmp_path / "settings.json")
    video = _make_synthetic_video(tmp_path / "partial.avi", frames=60)
    config = AnalysisConfig(roi=(0, 0.25, 1, 0.5), flow_direction=(1, 0), background_samples=21)
    analyzer = DropletAnalyzer(config)
    stop = {"value": False}

    def progress(done, total):
        if done >= total // 3:
            stop["value"] = True

    partial = analyzer.analyze(video, progress_callback=progress, cancel_check=lambda: stop["value"])
    assert not partial.complete

    # save_result refuses to persist an incomplete run, and a well-formed
    # cached result with complete=false is rejected by load_cached.
    assert store.save_result(partial) is None
    assert store.load_cached(video) is None
    planted_dir = store.result_directory(video)
    planted_dir.mkdir(parents=True, exist_ok=True)
    payload = partial.to_dict()
    payload["complete"] = False
    (planted_dir / "analysis.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    assert store.load_cached(video) is None
    assert store.load_cached(video, partial.cache_key) is None


def test_truncated_video_is_incomplete_and_never_cached(tmp_path):
    truncated = _make_truncated_video(tmp_path / "cut.avi", frames=150)
    config = AnalysisConfig(roi=(0, 0.25, 1, 0.5), flow_direction=(1, 0), background_samples=21)

    # Precondition: the header still advertises 150 frames, so only a run that
    # notices the short decode can tell this clip from a genuinely short one.
    source = VideoSource(truncated)
    assert source.metadata.frame_count == 150
    assert 0 < sum(1 for _ in source.iter_frames()) < 150
    assert source.decode_truncated
    source.release()

    result = DropletAnalyzer(config).analyze(truncated)
    assert result.complete is False
    assert result.processed_frames < result.metadata.frame_count
    # A partial run must not be persisted or replayed as an authoritative answer.
    store = ResultStore(tmp_path / "analytics", tmp_path / "settings.json")
    assert store.save_result(result) is None
    assert store.load_cached(truncated) is None

    # The same fixture, intact, must still be a complete run: the new check keys
    # on ending early, not on the analysis itself.
    intact = _make_synthetic_video(tmp_path / "intact.avi", frames=150)
    healthy = DropletAnalyzer(config).analyze(intact)
    assert healthy.complete is True
    assert healthy.processed_frames == healthy.metadata.frame_count
    assert store.save_result(healthy) is not None


def test_load_settings_falls_back_to_defaults_for_non_dict_payloads(tmp_path):
    store = ResultStore(tmp_path / "analytics", tmp_path / "settings.json")
    defaults = AnalysisConfig()

    # Valid JSON that is not a settings object: AnalysisConfig.from_dict calls
    # data.get(...), whose AttributeError must not escape page construction.
    for payload in ("null", "[1, 2, 3]", '"settings"', "17"):
        store.settings_path.write_text(payload, encoding="utf-8")
        loaded = store.load_settings()
        assert loaded == defaults, payload

    # Pre-existing guards must keep working for the malformed cases too.
    for payload in ("{not json", ""):
        store.settings_path.write_text(payload, encoding="utf-8")
        assert store.load_settings() == defaults


def test_build_background_reports_skipped_samples_when_frame_reads_fail(tmp_path):
    video = _make_synthetic_video(tmp_path / "bg.avi", frames=120)
    config = AnalysisConfig(background_samples=21)
    roi_px = (0, 60, 640, 120)

    healthy = build_background(VideoSource(video), config, roi_px)
    assert healthy.requested_samples == 21
    assert healthy.skipped_samples == 0

    flaky = build_background(_TailUndecodableSource(video), config, roi_px)
    # Indices past the midpoint fail, so exactly half of the 21 samples drop.
    assert flaky.requested_samples == 21
    assert flaky.skipped_samples == 10
    # Reporting the drop must not stop it producing a usable background.
    assert flaky.background_gray.shape == healthy.background_gray.shape
    assert flaky.background_bgr.shape == healthy.background_bgr.shape


def test_analyzer_surfaces_background_sample_skips_in_result(tmp_path, monkeypatch):
    video = _make_synthetic_video(tmp_path / "propagate.avi", frames=120)
    config = AnalysisConfig(roi=(0, 0.25, 1, 0.5), flow_direction=(1, 0), background_samples=21)

    clean = DropletAnalyzer(config).analyze(video)
    assert clean.skipped_frames == 0

    monkeypatch.setattr(analyzer_module, "VideoSource", _TailUndecodableSource)
    degraded = DropletAnalyzer(config).analyze(video)
    # The background samples are part of the run's skipped-frame tally rather
    # than being discarded inside the estimator.
    assert degraded.skipped_frames >= 10


def test_background_estimate_gate_tolerates_only_a_few_lost_samples():
    gate = analyzer_module.background_estimate_is_degraded

    assert gate(0, 21) is False
    # Losing the tail of an otherwise healthy sample grid is common and the
    # median still spans the recording, so the run must stay cacheable.
    assert gate(2, 21) is False
    # Past the tolerated share the survivors can all sit at one end of the clip.
    assert gate(10, 21) is True
    assert gate(20, 21) is True
    # Short clips clamp to a handful of samples, so the absolute floor bites.
    assert gate(3, 5) is True
    # A hand-built context records no grid: only a real skip is judgeable.
    assert gate(0, 0) is False
    assert gate(1, 0) is True


def test_degraded_background_estimate_demotes_the_run_and_blocks_caching(tmp_path, monkeypatch):
    video = _make_synthetic_video(tmp_path / "skewed.avi", frames=120)
    config = AnalysisConfig(roi=(0, 0.25, 1, 0.5), flow_direction=(1, 0), background_samples=21)
    store = ResultStore(tmp_path / "analytics", tmp_path / "settings.json")

    # Precondition: sequential decoding of the clip itself is unaffected, so the
    # whole frame pass completes and nothing but a background-integrity gate can
    # distinguish this run from a healthy one.
    monkeypatch.setattr(analyzer_module, "VideoSource", _SparseUndecodableSource)
    degraded = DropletAnalyzer(config).analyze(video)
    assert degraded.processed_frames == degraded.metadata.frame_count
    assert degraded.skipped_frames >= 18
    assert degraded.complete is False
    assert store.save_result(degraded) is None
    assert store.load_cached(video) is None

    # Same clip with a decodable sample grid: counting the skips must not demote a
    # healthy run.
    monkeypatch.setattr(analyzer_module, "VideoSource", VideoSource)
    healthy = DropletAnalyzer(config).analyze(video)
    assert healthy.skipped_frames == 0
    assert healthy.complete is True
    assert store.save_result(healthy) is not None
    assert store.load_cached(video, healthy.cache_key) is not None


def test_a_few_unreadable_background_samples_stay_complete_and_cached(tmp_path, monkeypatch):
    video = _make_synthetic_video(tmp_path / "marginal.avi", frames=120)
    config = AnalysisConfig(roi=(0, 0.25, 1, 0.5), flow_direction=(1, 0), background_samples=21)
    store = ResultStore(tmp_path / "analytics", tmp_path / "settings.json")

    monkeypatch.setattr(analyzer_module, "VideoSource", _MarginallyUndecodableSource)
    result = DropletAnalyzer(config).analyze(video)
    # The degradation is still visible in the result, but two lost samples out of
    # twenty-one leave a median that spans the recording.
    assert 0 < result.skipped_frames <= 2
    assert result.complete is True
    assert store.save_result(result) is not None


def test_estimation_read_gate_tolerates_only_a_few_lost_reads():
    gate = analyzer_module.estimation_is_degraded

    assert gate(0, 24) is False
    # Losing the tail of an otherwise healthy read grid is common and the
    # surviving blocks still span the recording, so the run must stay cacheable.
    assert gate(2, 24) is False
    # Past the tolerated share the survivors can all sit at one end of the clip.
    assert gate(8, 24) is True
    assert gate(24, 24) is True
    # Short grids (the five-frame event-geometry window) are floored in absolute
    # terms: two of five reads lost leaves three readable frames, not enough.
    assert gate(1, 5) is False
    assert gate(2, 5) is True
    # A helper that needed no frame at all has no degradation to judge.
    assert gate(0, 0) is False
    assert gate(1, 0) is True


def test_estimation_reads_are_charged_once_per_lost_frame():
    analyzer = DropletAnalyzer(AnalysisConfig(background_samples=21))

    # The ROI sweep, the flow blocks and the event-geometry window all sample the
    # same recording, so one unreadable frame is routinely hit more than once.
    analyzer._record_estimation_reads([4, 9], 8)
    assert analyzer._skipped_frames == 2
    assert analyzer._estimation_degraded is False

    analyzer._record_estimation_reads([9, 12], 8)
    # Frame 9 is charged once: it is one frame lost, not two.
    assert analyzer._skipped_frames == 3
    assert analyzer._failed_frame_indices == {4, 9, 12}

    # A grid whose evidence is mostly gone gates the run even though the frame
    # it charges may already have been counted above.
    analyzer._record_estimation_reads([4, 9, 12, 20, 21, 22], 8)
    assert analyzer._skipped_frames == 6
    assert analyzer._estimation_degraded is True


def test_flow_direction_is_unknown_when_the_recording_shows_no_motion(tmp_path, monkeypatch):
    video = _make_synthetic_video(tmp_path / "nomotion.avi", frames=120)
    config = AnalysisConfig(roi=(0, 0.25, 1, 0.5), flow_direction=None, background_samples=21)

    # Precondition: on this clip the estimator does find motion, so a fabricated
    # direction would be indistinguishable from a measured one.
    source = VideoSource(video)
    analyzer = DropletAnalyzer(config)
    detector = analyzer._detector_for(source, config)
    measured = analyzer._estimate_flow_direction(source, detector)
    assert measured is not None
    source.release()

    # A recording with nothing moving yields no vector and no contour
    # orientation: the direction is unknown, not "left to right".
    blind = DropletAnalyzer(config)
    blind_detector = blind._detector_for(VideoSource(video), config)
    monkeypatch.setattr(blind_detector, "detect", lambda frame: [])
    assert blind._estimate_flow_direction(VideoSource(video), blind_detector) is None

    # End to end the unknown state has to be visible in the result itself.
    monkeypatch.setattr(DropletDetector, "detect", lambda self, frame: [])
    result = DropletAnalyzer(config).analyze(video)
    assert result.flow_direction is None
    assert result.complete is False
    # Nothing failed to decode here, so the demotion is purely "no evidence".
    assert result.skipped_frames == 0
    payload = result.to_dict()
    assert payload["flow_direction"] is None
    assert AnalysisResult.from_dict(payload).flow_direction is None
    store = ResultStore(tmp_path / "analytics", tmp_path / "settings.json")
    assert store.save_result(result) is None

    # Over-trigger guard: a clip that does move keeps its measured direction.
    monkeypatch.undo()
    healthy = DropletAnalyzer(config).analyze(video)
    assert healthy.flow_direction is not None
    assert healthy.complete is True
    assert store.save_result(healthy) is not None


def test_unreadable_estimation_samples_demote_the_run_and_block_caching(tmp_path, monkeypatch):
    video = _make_synthetic_video(tmp_path / "starved.avi", frames=120)
    # Auto mode from a full frame is what runs both the channel-ROI sweep and the
    # flow estimator before the frame pass.
    config = AnalysisConfig(mode="auto", flow_direction=None, background_samples=21)
    store = ResultStore(tmp_path / "analytics", tmp_path / "settings.json")

    # Only the background grid stays readable, so this is not the background gate
    # tripping again: it is exactly the hole that gate does not cover.
    monkeypatch.setattr(
        _PartialUndecodableSource, "allowed", _background_grid(120, 21)
    )
    monkeypatch.setattr(analyzer_module, "VideoSource", _PartialUndecodableSource)
    analyzer = DropletAnalyzer(config)
    degraded = analyzer.analyze(video)

    # Precondition: the sequential frame pass is unaffected, so the run looks
    # complete to everything except a helper-integrity gate.
    assert degraded.processed_frames == degraded.metadata.frame_count
    assert analyzer._background_degraded is False
    assert analyzer._estimation_degraded is True
    assert degraded.skipped_frames == len(analyzer._failed_frame_indices) > 0
    assert degraded.complete is False
    assert store.save_result(degraded) is None
    assert store.load_cached(video) is None

    # Over-trigger guard: the same clip with readable estimates stays cacheable.
    monkeypatch.setattr(analyzer_module, "VideoSource", VideoSource)
    healthy_analyzer = DropletAnalyzer(config)
    healthy = healthy_analyzer.analyze(video)
    assert healthy.skipped_frames == 0
    assert healthy_analyzer._estimation_degraded is False
    assert healthy.complete is True
    assert healthy.flow_direction is not None
    assert store.save_result(healthy) is not None
    assert store.load_cached(video, healthy.cache_key) is not None


def test_event_geometry_window_skips_are_charged_and_gated(tmp_path):
    video = _make_synthetic_video(tmp_path / "geometry.avi", frames=120)
    config = AnalysisConfig(roi=(0, 0.25, 1, 0.5), flow_direction=(1, 0), background_samples=21)

    analyzer = DropletAnalyzer(config)
    detector = analyzer._detector_for(VideoSource(video), config)
    analyzer._event_frame_geometry(VideoSource(video), detector, 2.0, (1.0, 0.0), 320.0, 70.0)
    assert analyzer._skipped_frames == 0
    assert analyzer._estimation_degraded is False

    # A repair window that cannot be read repairs nothing, and the measurement
    # keeps geometry nobody can vouch for: the run must not look authoritative.
    repaired = analyzer._event_frame_geometry(
        _UndecodableSource(video), detector, 2.0, (1.0, 0.0), 320.0, 70.0
    )
    assert repaired is None
    assert 0 < analyzer._skipped_frames <= 5
    assert analyzer._estimation_degraded is True


def test_unreadable_background_grid_returns_a_demoted_result_instead_of_raising(
    tmp_path, monkeypatch
):
    video = _make_synthetic_video(tmp_path / "nobackground.avi", frames=120)
    config = AnalysisConfig(roi=(0, 0.25, 1, 0.5), flow_direction=(1, 0), background_samples=21)
    store = ResultStore(tmp_path / "analytics", tmp_path / "settings.json")

    # Losing every sample is data quality, not a programming error: the estimate
    # reports it through the ordinary counts instead of aborting the analysis.
    unusable = build_background(_UndecodableSource(video), config, (0, 60, 640, 120))
    assert unusable.requested_samples == 21
    assert unusable.skipped_samples == 21
    # Shaped like a real background so the pipeline stays computable, but empty:
    # no geometry derived from it can pass as a valid measurement.
    assert unusable.background_gray.shape == (120, 640)
    assert unusable.background_bgr.shape == (120, 640, 3)
    assert not unusable.background_gray.any()

    monkeypatch.setattr(analyzer_module, "VideoSource", _UndecodableSource)
    result = DropletAnalyzer(config).analyze(video)
    assert result.processed_frames == result.metadata.frame_count
    assert result.skipped_frames == 21
    assert result.complete is False
    assert store.save_result(result) is None

    # Over-trigger guard: the same clip with a readable grid is still complete.
    monkeypatch.setattr(analyzer_module, "VideoSource", VideoSource)
    healthy = DropletAnalyzer(config).analyze(video)
    assert healthy.skipped_frames == 0
    assert healthy.complete is True
    assert store.save_result(healthy) is not None


def test_skipped_frame_counter_is_usable_before_analyze_runs(tmp_path):
    video = _make_synthetic_video(tmp_path / "incremental.avi", frames=120)
    config = AnalysisConfig(roi=(0, 0.25, 1, 0.5), background_samples=21)

    analyzer = DropletAnalyzer(config)
    # Class-level defaults: reading these counters on a freshly constructed
    # analyzer must not raise AttributeError.
    assert DropletAnalyzer._skipped_frames == 0
    assert DropletAnalyzer._background_degraded is False
    assert analyzer._skipped_frames == 0
    assert analyzer._background_degraded is False

    clean = analyzer._detector_for(VideoSource(video), config)
    assert clean.context.background_skipped_samples == 0
    assert analyzer._skipped_frames == 0

    # The degraded path charges the same counter, again without analyze().
    degraded = DropletAnalyzer(config)
    detector = degraded._detector_for(_TailUndecodableSource(video), config)
    assert detector.context.background_requested_samples == 21
    assert degraded._skipped_frames == 10
    assert degraded._background_degraded is True

    # The counters are per-run state: a charge never leaks into the next run.
    analyzer._skipped_frames += 3
    assert analyzer._skipped_frames == 3
    reusable = DropletAnalyzer(config)
    reusable.analyze(video)
    assert reusable._skipped_frames == 0
    assert reusable._background_degraded is False


def test_auto_roi_does_not_mutate_the_user_config(tmp_path):
    video = _make_synthetic_video(tmp_path / "auto.avi")
    analyzer = DropletAnalyzer(AnalysisConfig(mode="auto", background_samples=21))
    result = analyzer.analyze(video)
    assert result.complete
    # The auto-detected band is a run outcome (roi_px), not a setting: the
    # persisted config must keep full-frame ROI so the next auto run can
    # re-detect.
    assert result.config.roi == (0.0, 0.0, 1.0, 1.0)
    assert result.roi_px[1] > 0 or result.roi_px[3] < result.metadata.height


def test_result_directories_do_not_collide_by_filename_stem(tmp_path):
    store = ResultStore(tmp_path / "analytics", tmp_path / "settings.json")
    (tmp_path / "a").mkdir(parents=True, exist_ok=True)
    (tmp_path / "b").mkdir(parents=True, exist_ok=True)
    first = _make_synthetic_video(tmp_path / "a" / "run.avi", frames=10)
    second = _make_synthetic_video(tmp_path / "b" / "run.avi", frames=10)
    assert store.result_directory(first) != store.result_directory(second)


def test_result_store_round_trips_settings_and_cached_result(tmp_path):
    video = _make_synthetic_video(tmp_path / "cache.avi")
    config = AnalysisConfig(roi=(0, 0.25, 1, 0.5), flow_direction=(1, 0))
    result = DropletAnalyzer(config).analyze(video)
    store = ResultStore(tmp_path / "analytics", tmp_path / "settings.json")
    store.save_settings(config)
    assert store.load_settings().roi == config.roi
    store.save_result(result)
    cached = store.load_cached(video, result.cache_key)
    assert cached is not None
    assert cached.summary.total_droplets == result.summary.total_droplets
    assert cached.overlays.keys() == result.overlays.keys()


def test_csv_is_human_readable_and_keeps_machine_metadata_out_of_rows(tmp_path):
    video = _make_synthetic_video(tmp_path / "export.avi")
    result = DropletAnalyzer(AnalysisConfig(roi=(0, 0.25, 1, 0.5), flow_direction=(1, 0))).analyze(video)
    target = export_result_csv(result, tmp_path / "result.csv")
    with target.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    assert tuple(rows[0].keys()) == CSV_FIELDS
    assert len(rows) == len(result.measurements)
    assert rows[0]["Length (px)"]
    assert rows[0]["Status"] in {"Valid", "Rejected"}
    assert "source_video" not in rows[0]
    assert "analysis_version" not in rows[0]
