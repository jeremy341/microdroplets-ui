"""Static regression checks for GUI-thread performance boundaries."""

import ast
from pathlib import Path
import unittest


CAMERA_SOURCE = Path(__file__).resolve().parents[1] / "ui" / "pages" / "Camera.py"


class CameraPerformanceArchitectureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = CAMERA_SOURCE.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)

    def _method_source(self, class_name, method_name):
        for node in self.tree.body:
            if isinstance(node, ast.ClassDef) and node.name == class_name:
                for child in node.body:
                    if isinstance(child, ast.FunctionDef) and child.name == method_name:
                        return ast.get_source_segment(self.source, child)
        self.fail(f"{class_name}.{method_name} was not found")

    def test_resolution_and_fps_handlers_only_queue_worker_mode(self):
        resolution = self._method_source("CameraPage", "apply_resolution")
        fps = self._method_source("CameraPage", "apply_fps")
        self.assertIn("_queue_mode_change", resolution)
        self.assertIn("_queue_mode_change", fps)
        self.assertNotIn("camera.set_resolution", resolution)
        self.assertNotIn("camera.set_fps", fps)

    def test_video_writer_exists_only_in_recorder_worker(self):
        start_recording = self._method_source("CameraPage", "start_recording")
        recorder_run = self._method_source("RecorderWorker", "run")
        self.assertNotIn("cv2.VideoWriter(", start_recording)
        self.assertIn("cv2.VideoWriter(", recorder_run)

    def test_live_exposure_updates_are_unverified_until_release(self):
        live_flush = self._method_source("CameraPage", "_flush_pending_exposure")
        final_flush = self._method_source("CameraPage", "_flush_exposure_now")
        self.assertIn("exposure_readback=False", live_flush)
        self.assertIn("exposure_readback=True", final_flush)

    def test_custom_slider_uses_position_during_drag_and_has_one_release_path(self):
        slider = self._method_source("CameraSlider", "_set_pointer_value")
        release = self._method_source("CameraSlider", "mouseReleaseEvent")
        self.assertIn("setSliderPosition", slider)
        self.assertNotIn("self.sliderReleased.emit()", release)
        self.assertIn("setSliderDown(False)", release)

    def test_live_readback_cannot_reconcile_exposure_thumb(self):
        result_handler = self._method_source("CameraPage", "_camera_property_result")
        self.assertIn("and _exposure_readback", result_handler)

    def test_preview_consumes_latest_frame_only(self):
        pull = self._method_source("CameraPage", "_pull_latest_frame")
        self.assertIn("take_latest_frame", pull)

    def test_fixed_am4113t_fps_presets_are_not_profile_driven(self):
        refresh = self._method_source("CameraPage", "_refresh_fps_options_for_resolution")
        self.assertIn("am4113t_fps_options", refresh)
        self.assertNotIn("modes_for_resolution", refresh)
        self.assertNotIn("last_measured_fps", refresh)
        self.assertNotIn("stable_fps", refresh)

    def test_worker_has_no_runtime_fps_probe_or_measurement(self):
        worker_source = self._method_source("CameraWorker", "run")
        self.assertIn("_frame_interval", worker_source)
        self.assertIn("_next_frame_deadline", worker_source)
        self.assertNotIn("_report_performance", worker_source)
        self.assertNotIn("_run_fps_probe", self.source)
        self.assertNotIn("request_fps_probe", self.source)
        self.assertNotIn("Measuring FPS", self.source)

    def test_preview_timer_follows_selected_fixed_preset(self):
        sync = self._method_source("CameraPage", "_sync_preview_timer_to_selected_fps")
        fps = self._method_source("CameraPage", "apply_fps")
        resolution = self._method_source("CameraPage", "apply_resolution")
        self.assertIn("1000 / max(1, self._fps())", sync)
        self.assertIn("_sync_preview_timer_to_selected_fps", fps)
        self.assertIn("_sync_preview_timer_to_selected_fps", resolution)

    def test_recording_uses_selected_fps_directly(self):
        start_recording = self._method_source("CameraPage", "start_recording")
        self.assertIn("selected_fps = self._fps()", start_recording)
        self.assertIn("RecorderWorker(path, float(selected_fps)", start_recording)
        self.assertNotIn("measured_fps", start_recording)

    def test_resolution_change_refreshes_fixed_fps_presets(self):
        resolution = self._method_source("CameraPage", "apply_resolution")
        result = self._method_source("CameraPage", "_camera_mode_result")
        self.assertIn("_refresh_fps_options_for_resolution", resolution)
        self.assertIn("_refresh_fps_options_for_resolution", result)
        self.assertNotIn("_request_fps_probe_once", result)

    def test_camera_discovery_is_manual_and_reuses_status_control(self):
        constructor = self._method_source("CameraPage", "__init__")
        build_ui = self._method_source("CameraPage", "_build_ui")
        self.assertNotIn("self.refresh_devices()", constructor)
        self.assertIn('QPushButton("Search")', build_ui)
        self.assertIn("self.status.clicked.connect(self.refresh_devices)", build_ui)


if __name__ == "__main__":
    unittest.main()
