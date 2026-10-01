from __future__ import annotations

import csv
import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from backend.analytics import AnalysisConfig, DropletAnalyzer
from backend.analytics.calibration import manual_scale, scale_from_reference
from backend.analytics.csv_export import CSV_FIELDS, export_result_csv
from backend.analytics.detector import apply_flow_geometry
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
