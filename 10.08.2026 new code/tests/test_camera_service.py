import unittest

from backend.camera_service import CameraDevice, DinoLiteSdkBridge, OpenCVCamera, enumerate_cameras


class CameraServiceTests(unittest.TestCase):
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

    def test_sdk_is_optional(self):
        bridge = DinoLiteSdkBridge()
        self.assertIsInstance(bridge.available, bool)
        self.assertFalse(bridge.set_led_enabled(True))
