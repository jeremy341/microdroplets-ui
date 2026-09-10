"""Regression checks for the shared Camera/Workspace preview runtime."""

import ast
from pathlib import Path

from backend.camera_service import OpenCVCamera


ROOT = Path(__file__).resolve().parents[1]
CAMERA = (ROOT / "ui" / "pages" / "Camera.py").read_text(encoding="utf-8")
WORKSPACE = (ROOT / "ui" / "pages" / "Workspace.py").read_text(encoding="utf-8")
SERVICE = (ROOT / "backend" / "camera_service.py").read_text(encoding="utf-8")


def _method_source(source, class_name, method_name):
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for child in node.body:
                if isinstance(child, ast.FunctionDef) and child.name == method_name:
                    return ast.get_source_segment(source, child)
    raise AssertionError(f"{class_name}.{method_name} not found")


def test_opencv_camera_exposes_a_non_consuming_shared_frame_store():
    frame = object()
    camera = OpenCVCamera()

    camera.publish_latest_frame(frame)

    assert camera.latest_frame() is frame
    assert camera.latest_frame() is frame  # observing it does not consume it
    camera.clear_latest_frame()
    assert camera.latest_frame() is None


def test_workspace_reads_shared_runtime_frame_not_camera_page_render_cache():
    refresh = _method_source(WORKSPACE, "CameraWorkspacePanel", "refresh_from_backend")
    assert "page.camera.latest_frame()" in refresh
    assert "frame = page.last_frame" not in refresh


def test_worker_publishes_only_accepted_frames_to_shared_runtime():
    run = _method_source(CAMERA, "CameraWorker", "run")
    assert "if self._discard_frames" in run
    assert run.index("self.camera.publish_latest_frame(frame)") > run.index("if self._discard_frames")


def test_camera_hidden_page_does_not_block_frame_state_updates():
    pull = _method_source(CAMERA, "CameraPage", "_pull_latest_frame")
    assert "take_latest_frame" in pull
    assert "or not self.isVisible()" not in pull
    assert "self.last_frame = frame" in pull


def test_capture_and_record_use_shared_current_frame():
    capture = _method_source(CAMERA, "CameraPage", "capture_photo")
    record = _method_source(CAMERA, "CameraPage", "start_recording")
    current = _method_source(CAMERA, "CameraPage", "_current_frame")
    assert "self.camera.latest_frame()" in current
    assert "frame = self._current_frame()" in capture
    assert "frame = self._current_frame()" in record


def test_both_previews_share_fill_and_crop_renderer():
    assert "def camera_frame_pixmap" in CAMERA
    assert "Qt.AspectRatioMode.KeepAspectRatioByExpanding" in CAMERA
    refresh = _method_source(WORKSPACE, "CameraWorkspacePanel", "refresh_from_backend")
    assert "camera_frame_pixmap(frame, self.preview.size())" in refresh
    assert "Qt.AspectRatioMode.KeepAspectRatio" not in refresh


def test_camera_service_documentation_reference_uses_current_camera_doc():
    assert "docs/DEVELOPER_GUIDE.md" in SERVICE
