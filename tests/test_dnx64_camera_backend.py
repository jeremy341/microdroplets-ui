"""Hardware-independent tests for the production hybrid camera backend."""

from __future__ import annotations

import unittest

from backend.camera_service import EFFECTIVE_EXPOSURE_MAX_RAW, OpenCVCamera


class FakeCapture:
    def isOpened(self):
        return True

    def release(self):
        pass


class FakeDNX64:
    def __init__(self):
        self.is_initialized = True
        self.calls = []
        self.brightness = 128
        self.exposure = 256
        self.auto_exposure = 1

    def brightness_range(self):
        self.calls.append(("brightness_range",))
        return 0, 255

    def get_auto_exposure(self):
        self.calls.append(("get_auto_exposure",))
        return self.auto_exposure

    def set_auto_exposure(self, enabled):
        self.calls.append(("set_auto_exposure", enabled))
        self.auto_exposure = int(enabled)
        return self.auto_exposure

    def get_brightness(self):
        self.calls.append(("get_brightness",))
        return self.brightness

    def set_brightness(self, value):
        self.calls.append(("set_brightness", value))
        self.brightness = value
        return value

    def get_exposure(self):
        self.calls.append(("get_exposure",))
        return self.exposure

    def set_exposure(self, value, *, readback=True):
        self.calls.append(("set_exposure", value, readback))
        self.exposure = value
        return value

    def set_led(self, enabled):
        self.calls.append(("set_led", enabled))

    def close(self):
        self.is_initialized = False


class ProductionDnx64BackendTests(unittest.TestCase):
    def make_camera(self):
        camera = OpenCVCamera()
        camera._capture = FakeCapture()
        camera.device_index = 0
        camera._dnx64 = FakeDNX64()
        camera.capabilities = camera._build_capabilities(
            "Dino-Lite Premier",
            r"\\?\usb#vid_a168&pid_0870&mi_00#test",
            4,
        )
        return camera

    def test_capability_profile_contains_all_required_fields(self):
        camera = self.make_camera()
        capabilities = camera.get_capabilities()
        self.assertTrue(capabilities.connected)
        self.assertEqual(capabilities.camera_name, "Dino-Lite Premier")
        self.assertIn("pid_0870", capabilities.device_id)
        self.assertEqual(capabilities.brightness_raw_max, 255)
        self.assertEqual(capabilities.exposure_raw_max, 41771)
        self.assertTrue(capabilities.auto_exposure_supported)
        self.assertTrue(capabilities.led_supported)
        self.assertEqual(capabilities.supported_resolutions[0], (1280, 1024))
        self.assertEqual(capabilities.supported_fps, (10, 20, 30))

    def test_brightness_percent_is_converted_to_raw_dnx64_value(self):
        camera = self.make_camera()
        result = camera.set_brightness_percent(100)
        self.assertTrue(result.supported)
        self.assertIn(("set_brightness", 255), camera._dnx64.calls)
        self.assertEqual(result.applied, 100.0)

    def test_exposure_disables_auto_before_writing_raw_value(self):
        camera = self.make_camera()
        result = camera.set_exposure(100)
        self.assertTrue(result.supported)
        calls = camera._dnx64.calls
        disable_position = calls.index(("set_auto_exposure", False))
        exposure_position = calls.index(
            ("set_exposure", EFFECTIVE_EXPOSURE_MAX_RAW, True)
        )
        self.assertLess(disable_position, exposure_position)
        self.assertNotIn(("get_exposure",), calls[disable_position + 1 : exposure_position])

    def test_led_calls_dnx64_without_ui_dependency(self):
        camera = self.make_camera()
        result = camera.set_led_enabled(False)
        self.assertTrue(result.supported)
        self.assertIn(("set_led", False), camera._dnx64.calls)


if __name__ == "__main__":
    unittest.main()
