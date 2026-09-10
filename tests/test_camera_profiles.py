import tempfile
import unittest
import json
from pathlib import Path

from backend.camera_profiles import CameraProfileBuilder, CameraProfileStore


class CameraProfileStoreTests(unittest.TestCase):
    def test_uses_35_percent_fps_loss_threshold(self):
        with tempfile.TemporaryDirectory() as directory:
            store = CameraProfileStore(Path(directory) / "profiles.json")
            mode = store.observe_fps("device", "Dino-Lite", 640, 480, 30, 30.5)
            self.assertEqual(mode["baseline_fps"], 30.5)
            self.assertAlmostEqual(mode["minimum_allowed_fps"], 19.82, places=2)

    def test_stable_baseline_does_not_preserve_one_old_peak(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profiles.json"
            store = CameraProfileStore(path)
            store.observe_fps("device", "Dino-Lite", 1280, 1024, 30, 8.4)
            mode = store.observe_fps("device", "Dino-Lite", 1280, 1024, 30, 4.8)
            self.assertEqual(mode["baseline_fps"], 6.6)
            self.assertEqual(mode["last_measured_fps"], 4.8)

            loaded = CameraProfileStore(path).mode("device", 1280, 1024, 30)
            self.assertEqual(loaded["baseline_fps"], 6.6)

    def test_legacy_peak_is_not_used_as_new_stable_baseline(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profiles.json"
            path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "devices": {
                            "device": {
                                "camera_name": "Dino-Lite",
                                "modes": {
                                    "640x480@30": {
                                        "width": 640,
                                        "height": 480,
                                        "target_fps": 30,
                                        "baseline_fps": 43.48,
                                        "last_measured_fps": 30.05,
                                    }
                                },
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            store = CameraProfileStore(path)
            mode = store.observe_fps("device", "Dino-Lite", 640, 480, 30, 30.1)
            self.assertEqual(mode["baseline_fps"], 30.08)
            self.assertEqual(mode["legacy_baseline_fps"], 43.48)
            self.assertEqual(mode["profile_version"], 2)

    def test_builder_persists_identity_mode_and_verified_readback(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profiles.json"
            store = CameraProfileStore(path)
            builder = CameraProfileBuilder(
                store,
                device_id="usb-camera-1",
                camera_name="Dino-Lite AM4113T R9",
                hardware_min=1,
                hardware_max=41771,
                practical_max=1045,
            )
            builder.ensure_mode(640, 480, 30)
            builder.observe_fps(640, 480, 30, 30.5)
            builder.record_exposure_readback(
                640, 480, 30, 40.0, 39.8, verified=True
            )

            loaded = CameraProfileStore(path)
            device = json.loads(path.read_text(encoding="utf-8"))["devices"]["usb-camera-1"]
            mode = loaded.mode("usb-camera-1", 640, 480, 30)
            self.assertEqual(device["hardware_exposure_max"], 41771)
            self.assertEqual(mode["minimum_allowed_fps"], 19.82)
            self.assertEqual(mode["practical_exposure_max"], 1045)
            self.assertTrue(mode["exposure_readback_verified"])


if __name__ == "__main__":
    unittest.main()
