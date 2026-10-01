import time
import unittest

import backend.camera_service as camera_service
from backend.camera_service import (
    CameraDevice,
    OpenCVCamera,
    documented_exposure_max,
    directshow_exposure_from_percent,
    enumerate_cameras,
    practical_exposure_max,
)


CAP_PROP_BRIGHTNESS = 10
CAP_PROP_EXPOSURE = 15
CAP_PROP_AUTO_EXPOSURE = 21


class FakeCapture:
    def __init__(self):
        self.values = {}

    def isOpened(self):
        return True

    def set(self, prop, value):
        self.values[prop] = value
        return True

    def get(self, prop):
        return self.values.get(prop, 0.0)

    def release(self):
        pass


class DelayedBrightnessCapture(FakeCapture):
    """Return the previous brightness once after every write."""

    def __init__(self):
        super().__init__()
        self._reported_brightness = 0.0
        self._pending_brightness = None

    def set(self, prop, value):
        if prop == CAP_PROP_BRIGHTNESS:
            self._pending_brightness = float(value)
            self.values[prop] = float(value)
            return True
        return super().set(prop, value)

    def get(self, prop):
        if prop == CAP_PROP_BRIGHTNESS:
            reported = self._reported_brightness
            if self._pending_brightness is not None:
                self._reported_brightness = self._pending_brightness
                self._pending_brightness = None
            return reported
        return super().get(prop)


class NormalizedBrightnessCapture(FakeCapture):
    """Model a UVC driver that exposes brightness in the 0..1 range."""

    def set(self, prop, value):
        if prop == CAP_PROP_BRIGHTNESS:
            self.values[prop] = max(0.0, min(1.0, float(value)))
            return True
        return super().set(prop, value)


class ExposureCapture(FakeCapture):
    """Model a UVC capture with separate auto mode and manual exposure."""

    def __init__(self):
        super().__init__()
        self.values[CAP_PROP_AUTO_EXPOSURE] = 0.75
        self.values[CAP_PROP_EXPOSURE] = -6.0

    def set(self, prop, value):
        if prop == CAP_PROP_AUTO_EXPOSURE:
            # DirectShow convention used by OpenCVCamera.
            self.values[prop] = float(value)
            return True
        if prop == CAP_PROP_EXPOSURE:
            # Manual exposure is writable only after auto mode is disabled.
            if abs(float(self.values[CAP_PROP_AUTO_EXPOSURE]) - 0.25) > 0.02:
                return False
        return super().set(prop, value)


class CameraServiceTests(unittest.TestCase):
    def test_documented_exposure_ranges_are_model_specific(self):
        self.assertEqual(documented_exposure_max("Dino-Lite 3013", None), 30612)
        self.assertEqual(documented_exposure_max("Dino-Lite 1.3M Edge", None), 63076)
        self.assertEqual(documented_exposure_max("Dino-Lite 5M Premier", None), 30000)
        self.assertEqual(
            documented_exposure_max("Dino-Lite Premier", "USB\\VID_A168&PID_0870"),
            41771,
        )

    def test_practical_exposure_limit_is_1_25_percent_of_device_range(self):
        self.assertEqual(practical_exposure_max(1, 41771), 522)

    def test_camera_device_metadata_is_explicit(self):
        device = CameraDevice(2, "Dino-Lite AM4113T (R9)", 1280, 1024, 30.0)
        self.assertEqual(device.index, 2)
        self.assertEqual((device.width, device.height), (1280, 1024))

    def test_camera_wrapper_starts_closed(self):
        camera = OpenCVCamera()
        self.assertFalse(camera.is_open)
        camera.close()
        self.assertIsNone(camera.device_index)

    def test_enumeration_is_safe_without_hardware(self):
        devices = enumerate_cameras(max_devices=1)
        self.assertIsInstance(devices, list)

    def test_property_readback_preserves_zero(self):
        camera = OpenCVCamera()
        camera._capture = FakeCapture()
        self.assertEqual(camera.get_property(1, 99.0), 0.0)

    def test_property_write_is_verified_from_driver_readback(self):
        camera = OpenCVCamera()
        camera._capture = FakeCapture()
        result = camera.set_property_verified(1, 0.42)
        self.assertTrue(result.supported)
        self.assertEqual(result.applied, 0.42)

    @unittest.skipIf(camera_service.cv2 is None, "OpenCV is unavailable")
    def test_brightness_uses_normalized_driver_scale(self):
        camera = OpenCVCamera()
        camera._capture = NormalizedBrightnessCapture()
        camera._capture.values[CAP_PROP_BRIGHTNESS] = 0.50
        result = camera.set_brightness_percent(89)
        self.assertTrue(result.supported)
        self.assertEqual(result.applied, 0.89)

    @unittest.skipIf(camera_service.cv2 is None, "OpenCV is unavailable")
    def test_brightness_uses_percent_driver_scale(self):
        camera = OpenCVCamera()
        camera._capture = FakeCapture()
        camera._capture.values[CAP_PROP_BRIGHTNESS] = 50.0
        result = camera.set_brightness_percent(89)
        self.assertTrue(result.supported)
        self.assertEqual(result.applied, 89.0)

    @unittest.skipIf(camera_service.cv2 is None, "OpenCV is unavailable")
    def test_brightness_can_recover_after_zero_on_percent_driver(self):
        camera = OpenCVCamera()
        camera._capture = FakeCapture()
        camera._capture.values[CAP_PROP_BRIGHTNESS] = 50.0

        self.assertTrue(camera.set_brightness_percent(0).supported)
        self.assertEqual(camera._capture.values[CAP_PROP_BRIGHTNESS], 0.0)

        result = camera.set_brightness_percent(100)
        self.assertTrue(result.supported)
        self.assertEqual(camera._capture.values[CAP_PROP_BRIGHTNESS], 100.0)
        self.assertEqual(result.applied, 100.0)

    @unittest.skipIf(camera_service.cv2 is None, "OpenCV is unavailable")
    def test_brightness_can_recover_after_zero_on_normalized_driver(self):
        camera = OpenCVCamera()
        camera._capture = NormalizedBrightnessCapture()
        camera._capture.values[CAP_PROP_BRIGHTNESS] = 0.5

        self.assertTrue(camera.set_brightness_percent(0).supported)
        self.assertEqual(camera._capture.values[CAP_PROP_BRIGHTNESS], 0.0)

        result = camera.set_brightness_percent(100)
        self.assertTrue(result.supported)
        self.assertEqual(camera._capture.values[CAP_PROP_BRIGHTNESS], 1.0)
        self.assertEqual(result.applied, 1.0)

    @unittest.skipIf(camera_service.cv2 is None, "OpenCV is unavailable")
    def test_delayed_zero_readback_does_not_lock_the_wrong_brightness_scale(self):
        camera = OpenCVCamera()
        camera._capture = DelayedBrightnessCapture()
        camera._capture.values[CAP_PROP_BRIGHTNESS] = 0.0
        camera._reported_brightness = 0.0

        result = camera.set_brightness_percent(0)
        self.assertTrue(result.supported)
        self.assertIsNone(camera._brightness_scale)

        # The next request must still use the native 0..100 representation;
        # a stale readback of zero must not turn 100 into 1.0.
        result = camera.set_brightness_percent(100)
        self.assertTrue(result.supported)
        self.assertEqual(camera._capture.values[CAP_PROP_BRIGHTNESS], 100.0)
        self.assertEqual(camera._brightness_scale, "percent")

    @unittest.skipIf(camera_service.cv2 is None, "OpenCV is unavailable")
    def test_normalized_brightness_probe_reissues_requested_value(self):
        camera = OpenCVCamera()
        camera._capture = NormalizedBrightnessCapture()
        camera._capture.values[CAP_PROP_BRIGHTNESS] = 0.50

        result = camera.set_brightness_percent(89)

        self.assertTrue(result.supported)
        self.assertEqual(camera._capture.values[CAP_PROP_BRIGHTNESS], 0.89)
        self.assertEqual(result.applied, 0.89)

    @unittest.skipIf(camera_service.cv2 is None, "OpenCV is unavailable")
    def test_manual_exposure_requires_auto_mode_to_be_disabled_first(self):
        camera = OpenCVCamera()
        camera._capture = ExposureCapture()

        blocked = camera.set_property_verified(CAP_PROP_EXPOSURE, -3.0)
        self.assertFalse(blocked.supported)
        self.assertEqual(camera._capture.values[CAP_PROP_EXPOSURE], -6.0)

        mode = camera.set_auto_exposure(False)
        self.assertTrue(mode.supported)
        exposure = camera.set_property_verified(CAP_PROP_EXPOSURE, -3.0)
        self.assertTrue(exposure.supported)
        self.assertEqual(exposure.applied, -3.0)

    def test_directshow_exposure_percent_mapping_is_safe(self):
        self.assertEqual(directshow_exposure_from_percent(0), -13.0)
        self.assertEqual(directshow_exposure_from_percent(46), -7.0)
        self.assertEqual(directshow_exposure_from_percent(100), 0.0)

    @unittest.skipIf(camera_service.cv2 is None, "OpenCV is unavailable")
    def test_set_exposure_enables_manual_mode_before_writing_value(self):
        camera = OpenCVCamera()
        camera._capture = ExposureCapture()

        result = camera.set_exposure(85.0)

        self.assertTrue(result.supported)
        self.assertEqual(camera._capture.values[CAP_PROP_AUTO_EXPOSURE], 0.25)
        self.assertEqual(camera._capture.values[CAP_PROP_EXPOSURE], -2.0)
        self.assertEqual(result.applied, 85.0)

    @unittest.skipIf(camera_service.cv2 is None, "OpenCV is unavailable")
    def test_set_exposure_can_be_repeated_after_manual_mode_is_enabled(self):
        camera = OpenCVCamera()
        camera._capture = ExposureCapture()

        self.assertTrue(camera.set_exposure(69.0).supported)
        result = camera.set_exposure(92.0)

        self.assertTrue(result.supported)
        self.assertEqual(camera._capture.values[CAP_PROP_EXPOSURE], -1.0)
        self.assertEqual(result.applied, 92.0)

    @unittest.skipIf(camera_service.cv2 is None, "OpenCV is unavailable")
    def test_auto_exposure_mode_uses_driver_manual_and_auto_values(self):
        camera = OpenCVCamera()
        camera._capture = ExposureCapture()

        manual = camera.set_auto_exposure(False)
        self.assertTrue(manual.supported)
        self.assertEqual(manual.applied, 0.25)

        automatic = camera.set_auto_exposure(True)
        self.assertTrue(automatic.supported)
        self.assertEqual(automatic.applied, 0.75)


class SlowStaleCapture(FakeCapture):
    """Model a driver whose readbacks lag, forcing the verification retry."""

    def __init__(self):
        super().__init__()
        self.get_delay_s = 0.0
        self.values[CAP_PROP_BRIGHTNESS] = 1.0

    def set(self, prop, value):
        # The driver accepts the write but its readback never reflects it,
        # so verification retries for the full window.
        return True

    def get(self, prop):
        if self.get_delay_s:
            time.sleep(self.get_delay_s)
        return 1.0


class CameraServiceLockTests(unittest.TestCase):
    def test_property_verification_does_not_hold_the_lock_while_retrying(self):
        import threading
        import time as time_module

        camera = OpenCVCamera()
        capture = SlowStaleCapture()
        capture.get_delay_s = 0.04
        camera._capture = capture

        done = threading.Event()
        thread = threading.Thread(
            target=lambda: (camera.set_property_verified(CAP_PROP_BRIGHTNESS, 0.42), done.set()),
            daemon=True,
        )
        thread.start()
        try:
            if not done.wait(0.08):
                started = time_module.monotonic()
                camera.close()
                elapsed = time_module.monotonic() - started
                # The retry loop runs outside the service lock, so close()
                # must not wait for the whole verification window.
                self.assertLess(elapsed, 1.0)
                self.assertFalse(done.is_set())
            else:
                self.fail("verification finished before close() could interleave")
        finally:
            done.wait(2.0)
            thread.join(2.0)


class CameraProfileQuarantineTests(unittest.TestCase):
    def test_corrupt_profile_json_is_quarantined_not_silently_reset(self):
        import tempfile
        from pathlib import Path

        from backend.camera_profiles import CameraProfileStore

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "camera_profiles.json"
            path.write_text("{corrupted", encoding="utf-8")
            store = CameraProfileStore(path)
            store.ensure_device("dev-1", "Dino-Lite")
            corrupt_copy = path.with_suffix(".json.corrupt")
            self.assertTrue(corrupt_copy.exists())
            self.assertEqual(corrupt_copy.read_text(encoding="utf-8"), "{corrupted")
            # The fresh store is usable and persisted afterwards.
            self.assertTrue(path.is_file())

    def test_wrong_shape_profile_json_is_quarantined(self):
        import json
        import tempfile
        from pathlib import Path

        from backend.camera_profiles import CameraProfileStore

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "camera_profiles.json"
            path.write_text(json.dumps(["not", "a", "mapping"]), encoding="utf-8")
            store = CameraProfileStore(path)
            store.ensure_device("dev-1", "Dino-Lite")
            self.assertTrue(path.with_suffix(".json.corrupt").exists())


class Dnx64VendorContractTests(unittest.TestCase):
    def test_set_video_proc_amp_signature_matches_dnx64_header(self):
        import ctypes

        from backend.dnx64_vendor import METHOD_SIGNATURES

        self.assertEqual(
            METHOD_SIGNATURES["SetVideoProcAmp"],
            ([ctypes.c_int, ctypes.c_long], None),
        )

    def test_get_video_device_count_does_not_reinitialize_the_sdk(self):
        import unittest.mock

        import backend.dnx64_vendor as vendor

        fake = unittest.mock.MagicMock()
        fake.GetVideoDeviceCount.return_value = 2
        with unittest.mock.patch.object(vendor.ctypes, "CDLL", return_value=fake):
            dnx = vendor.DNX64("fake.dll")
            self.assertEqual(dnx.GetVideoDeviceCount(), 2)
        self.assertEqual(fake.Init.call_count, 0)
