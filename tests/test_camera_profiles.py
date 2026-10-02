import tempfile
import unittest
import json
import os
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


class CameraProfileStoreDurabilityTests(unittest.TestCase):
    """A profile save must be durable, or a power cut destroys every record."""

    def test_save_flushes_content_before_the_rename(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profiles.json"
            store = CameraProfileStore(path)
            store.ensure_device("dev", "Old Name")

            order = []
            real_fsync = os.fsync
            real_replace = Path.replace

            def tracking_fsync(fd):
                order.append("fsync")
                return real_fsync(fd)

            def tracking_replace(self, target):
                order.append("replace")
                # At the moment of the rename the previously saved file must
                # still be complete: a rename that outruns its content can
                # leave an empty file, which the next load would quarantine.
                json.loads(Path(target).read_text(encoding="utf-8"))
                return real_replace(self, target)

            os.fsync = tracking_fsync
            Path.replace = tracking_replace
            try:
                store.ensure_device("dev", "New Name")
            finally:
                os.fsync = real_fsync
                Path.replace = real_replace

            self.assertIn("fsync", order, "the staged file must be fsync'd")
            self.assertLess(
                order.index("fsync"),
                order.index("replace"),
                "content must reach the disk before the rename becomes visible",
            )
            self.assertEqual(store.device("dev")["camera_name"], "New Name")

    def test_failed_rename_leaves_no_temp_file_behind(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profiles.json"
            store = CameraProfileStore(path)
            real_replace = Path.replace

            def boom(self, target):
                raise OSError("replace failed")

            Path.replace = boom
            try:
                with self.assertRaises(OSError):
                    store.ensure_device("dev", "Dino-Lite")
            finally:
                Path.replace = real_replace

            self.assertEqual(
                sorted(item.name for item in Path(directory).iterdir()),
                [],
                "a failed save must not leak a .tmp file into user data",
            )

    def test_failed_write_leaves_no_temp_file_behind(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profiles.json"
            store = CameraProfileStore(path)
            real_fsync = os.fsync

            def boom(fd):
                raise OSError("no space left on device")

            os.fsync = boom
            try:
                with self.assertRaises(OSError):
                    store.ensure_device("dev", "Dino-Lite")
            finally:
                os.fsync = real_fsync

            # The staged content was already written to a real temp file by
            # the time the flush failed, so the cleanup path is exercised.
            self.assertEqual(
                sorted(item.name for item in Path(directory).iterdir()),
                [],
                "a failed write must not leak a .tmp file into user data",
            )


class CameraProfileStoreDeviceKeyTests(unittest.TestCase):
    """One normalisation rule for device keys, whatever the caller passes."""

    def test_non_string_device_id_is_visible_to_every_accessor(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profiles.json"
            store = CameraProfileStore(path)
            # ensure_mode is what records width/height, which is what
            # modes_for_resolution filters on.
            store.ensure_mode(2, 640, 480, 30)
            store.observe_fps(2, "Dino-Lite", 640, 480, 30, 30.5)

            self.assertEqual(store.device("2")["camera_name"], "Dino-Lite")
            self.assertEqual(store.device(2)["camera_name"], "Dino-Lite")
            self.assertEqual(store.mode("2", 640, 480, 30)["baseline_fps"], 30.5)
            self.assertEqual(store.mode(2, 640, 480, 30)["baseline_fps"], 30.5)
            self.assertEqual(
                [item["mode_key"] for item in store.modes_for_resolution(2, 640, 480)],
                ["640x480@30"],
            )
            # A single key must serve every spelling of the same device.
            self.assertEqual(list(store._data["devices"]), ["2"])
            self.assertEqual(list(json.loads(path.read_text(encoding="utf-8"))["devices"]), ["2"])

            reloaded = CameraProfileStore(path)
            self.assertEqual(reloaded.mode(2, 640, 480, 30)["baseline_fps"], 30.5)

    def test_writers_agree_with_readers_across_spellings(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profiles.json"
            store = CameraProfileStore(path)

            store.ensure_device(7, "Dino-Lite", hardware_max=41771)
            store.ensure_mode("7", 640, 480, 30)
            store.observe_fps("7", "Dino-Lite", 640, 480, 30, 30.5)
            store.record_exposure_readback(7, 640, 480, 30, 40.0, 39.8, verified=True)

            device = store.device(7)
            self.assertEqual(device["hardware_exposure_max"], 41771)
            mode = store.mode("7", 640, 480, 30)
            self.assertEqual(mode["practical_exposure_max"], 1045)
            self.assertEqual(mode["baseline_fps"], 30.5)
            self.assertTrue(mode["exposure_readback_verified"])
            self.assertEqual(list(store._data["devices"]), ["7"])


class CameraProfileStoreCorruptRecordTests(unittest.TestCase):
    """One bad record must not take down the store, which starts the camera."""

    @staticmethod
    def _store(directory, modes, version=2):
        path = Path(directory) / "profiles.json"
        path.write_text(
            json.dumps({"version": version, "devices": {"d": {"camera_name": "X", "modes": modes}}}),
            encoding="utf-8",
        )
        return CameraProfileStore(path)

    def test_modes_for_resolution_skips_unreadable_records(self):
        with tempfile.TemporaryDirectory() as directory:
            store = self._store(
                directory,
                {
                    # width/height unreadable: cannot match any resolution.
                    "null@30": {"width": None, "height": 480, "target_fps": 30},
                    "abc@30": {"width": "abc", "height": 480, "target_fps": 30},
                    "list@30": {"width": [640], "height": 480, "target_fps": 30},
                    "dict@30": {"width": {"w": 640}, "height": 480, "target_fps": 30},
                    # not a dict at all.
                    "scalar@30": 42,
                    # Valid, and returned in target_fps order.
                    "ok@60": {"width": 640, "height": 480, "target_fps": 60},
                    "ok@30": {"width": 640, "height": 480, "target_fps": 30},
                },
            )
            result = store.modes_for_resolution("d", 640, 480)
            self.assertEqual(
                [item["mode_key"] for item in result],
                ["ok@30", "ok@60"],
            )

    def test_modes_for_resolution_survives_unreadable_target_fps(self):
        with tempfile.TemporaryDirectory() as directory:
            store = self._store(
                directory,
                {
                    "bad@30": {"width": 640, "height": 480, "target_fps": "zzz"},
                    "ok@30": {"width": 640, "height": 480, "target_fps": 30},
                },
            )
            result = store.modes_for_resolution("d", 640, 480)
            self.assertEqual(
                sorted(item["mode_key"] for item in result),
                ["bad@30", "ok@30"],
            )

    def test_observe_fps_survives_unreadable_fps_fields(self):
        corrupt = {
            "baseline_fps:null": {"baseline_fps": None},
            "baseline_fps:str": {"baseline_fps": "abc"},
            "baseline_fps:list": {"baseline_fps": [1, 2]},
            "fps_samples:int": {"fps_samples": 5},
            "fps_samples:none": {"fps_samples": None},
            "fps_samples:str": {"fps_samples": "30"},
            "fps_samples:bad": {"fps_samples": ["nope", None, 30.0]},
            "peak_fps:str": {"baseline_fps": 10.0, "peak_fps": "abc"},
            "last_measured:str": {"baseline_fps": 10.0, "last_measured_fps": "abc"},
        }
        modes = {}
        for index, (label, record) in enumerate(corrupt.items()):
            key = CameraProfileStore.mode_key(index, index, 30)
            modes[key] = dict(record, label=label)

        with tempfile.TemporaryDirectory() as directory:
            store = self._store(directory, modes)

            for index, (label, _) in enumerate(corrupt.items()):
                mode = store.observe_fps("d", "X", index, index, 30, 30.0)
                self.assertEqual(mode["baseline_fps"], 30.0, label)
                self.assertEqual(mode["last_measured_fps"], 30.0, label)
                self.assertGreaterEqual(mode["peak_fps"], 30.0, label)
                self.assertTrue(mode["fps_samples"], label)
                for sample in mode["fps_samples"]:
                    self.assertIsInstance(sample, float, label)
                    self.assertGreater(sample, 0, label)

            # A corrupt sample string must not be read as its digits.
            self.assertNotIn(3.0, store.observe_fps("d", "X", 5, 5, 30, 30.0)["fps_samples"])

    def test_writes_repair_wrong_shaped_nested_records(self):
        with tempfile.TemporaryDirectory() as directory:
            store = self._store(
                directory,
                {"640x480@30": "not-a-dict", "800x600@30": {"width": 800, "height": 600}},
            )
            # A mode record that is not a dict is replaced, not raised on.
            mode = store.ensure_mode("d", 640, 480, 30)
            self.assertEqual(mode["width"], 640)
            self.assertEqual(mode["practical_exposure_max"], 1045)
            self.assertEqual(store.mode("d", 640, 480, 30)["width"], 640)
            # The healthy neighbour is untouched.
            self.assertEqual(
                store.modes_for_resolution("d", 800, 600)[0]["mode_key"],
                "800x600@30",
            )

    def test_unreadable_version_does_not_break_startup(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profiles.json"
            for version in ("abc", [2], {"a": 1}, None):
                path.write_text(
                    json.dumps({"version": version, "devices": {}}), encoding="utf-8"
                )
                store = CameraProfileStore(path)
                self.assertEqual(store._data["version"], 2)
                self.assertEqual(store.device("d"), {})

    def test_v1_profile_is_still_readable(self):
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
            self.assertEqual(store._data["version"], 2)
            self.assertEqual(
                store.modes_for_resolution("device", 640, 480)[0]["mode_key"],
                "640x480@30",
            )


class CameraProfileStoreQuarantineTests(unittest.TestCase):
    def test_unusable_file_is_quarantined_but_recoverable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profiles.json"
            path.write_text("{not json", encoding="utf-8")

            store = CameraProfileStore(path)
            self.assertEqual(store.device("d"), {})

            quarantined = Path(directory) / "profiles.json.corrupt"
            self.assertTrue(quarantined.exists())
            self.assertEqual(quarantined.read_text(encoding="utf-8"), "{not json")
            self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
