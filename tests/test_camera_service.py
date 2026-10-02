import threading
import time
import unittest
import unittest.mock

import backend.camera_service as camera_service
from backend.camera_service import (
    CAMERA_CLOSE_TIMEOUT_SECONDS,
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
        self.release_count = 0

    def isOpened(self):
        return True

    def set(self, prop, value):
        self.values[prop] = value
        return True

    def get(self, prop):
        return self.values.get(prop, 0.0)

    def release(self):
        self.release_count += 1


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


class RendezvousCapture(FakeCapture):
    """Hold each named writer's first driver readback until both have arrived.

    Two concurrent writers reach their first ``get`` while each holding a
    different service lock. Parking them there reproduces the historical ABBA
    window (writer holding ``_lock`` waiting for the verification lock while a
    verification holds the verification lock and waits for ``_lock``) instead of
    relying on scheduler luck. Writers are identified by thread name, so the
    fake needs no knowledge of which transaction it is part of.
    """

    def __init__(self, roles, wait_s=0.5):
        super().__init__()
        self._roles = set(roles)
        self._wait_s = wait_s
        self._rendezvoused = set()
        self._both_arrived = threading.Event()

    def get(self, prop):
        name = threading.current_thread().name
        if name in self._roles and name not in self._rendezvoused:
            self._rendezvoused.add(name)
            if self._rendezvoused >= self._roles:
                self._both_arrived.set()
            # Bounded: after the wait the writer continues, so a correct
            # implementation finishes and a broken one still deadlocks later.
            self._both_arrived.wait(self._wait_s)
        return super().get(prop)


class StaleReadbackCapture(FakeCapture):
    """Accept every write but never reflect it, so the retry loop runs fully."""

    def __init__(self, get_delay_s=0.005):
        super().__init__()
        self.get_delay_s = get_delay_s
        self.reads = 0
        self.retry_started = threading.Event()

    def get(self, prop):
        self.reads += 1
        if self.reads > 1:
            # The first readback of the transaction is done; everything after it
            # belongs to the async-apply retry loop.
            self.retry_started.set()
        time.sleep(self.get_delay_s)
        return 0.0


class BlockingRetryCapture(StaleReadbackCapture):
    """Park the retry loop's first readback while the service lock is held."""

    def __init__(self):
        super().__init__(get_delay_s=0.0)
        self.blocked = threading.Event()
        self.unblock = threading.Event()

    def get(self, prop):
        if self.reads == 2:
            # The identity check for this iteration already passed, so the
            # service lock is held for the whole of this readback.
            self.blocked.set()
            self.unblock.wait(2.0)
        return super().get(prop)


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


@unittest.skipIf(camera_service.cv2 is None, "OpenCV is unavailable")
class CameraLockOrderTests(unittest.TestCase):
    """The canonical order is ``_property_verify_lock`` -> ``_lock``.

    Taking them the other way round deadlocks two threads permanently, which is
    what froze the application until it had to be killed through Task Manager.
    """

    def _run_pair(self, targets, timeout=5.0):
        """Run named targets concurrently and fail if any of them wedges."""

        failures = []
        start = threading.Barrier(len(targets) + 1)

        def wrap(name, target):
            def runner():
                try:
                    start.wait(2.0)
                    target()
                except Exception as exc:  # surfaced by the assertion below
                    failures.append(f"{name}: {exc!r}")

            return runner

        threads = [
            threading.Thread(target=wrap(name, target), name=name, daemon=True)
            for name, target in targets
        ]
        for thread in threads:
            thread.start()
        start.wait(2.0)
        deadline = time.monotonic() + timeout
        for thread in threads:
            thread.join(max(0.0, deadline - time.monotonic()))
        alive = [thread.name for thread in threads if thread.is_alive()]
        self.assertEqual(alive, [], f"threads deadlocked: {alive}")
        self.assertEqual(failures, [])
        return threads

    def test_concurrent_property_writers_do_not_deadlock(self):
        camera = OpenCVCamera()
        camera._capture = RendezvousCapture(
            {"brightness-writer", "verify-writer"}
        )
        results = {}

        def brightness():
            results["brightness"] = camera.set_brightness_percent(50)

        def verified():
            results["verified"] = camera.set_property_verified(CAP_PROP_EXPOSURE, -3.0)

        # set_brightness_percent is the writer that used to hold ``_lock`` and
        # then wait for the verification lock; set_property_verified is the
        # path that took them the other way round.
        self._run_pair(
            [("brightness-writer", brightness), ("verify-writer", verified)]
        )

        self.assertTrue(results["brightness"].supported)
        self.assertTrue(results["verified"].supported)
        self.assertTrue(camera.is_open)

    def test_verification_and_close_do_not_deadlock(self):
        camera = OpenCVCamera()
        capture = RendezvousCapture({"verify-writer"})
        camera._capture = capture
        results = {}

        def verified():
            results["verified"] = camera.set_property_verified(CAP_PROP_EXPOSURE, -3.0)

        def close():
            results["closed"] = camera.close(timeout=2.0)

        self._run_pair([("verify-writer", verified), ("closer", close)])

        # close() may legitimately win the race; what must never happen is a
        # wedge or a silent failure without a reason.
        result = results["verified"]
        self.assertTrue(
            result.supported
            or result.message
            in ("camera is not open", "camera closed during verification"),
            f"unexpected verification outcome: {result}",
        )
        self.assertTrue(results["closed"])
        self.assertEqual(capture.release_count, 1)
        self.assertFalse(camera.is_open)


class CameraTeardownTests(unittest.TestCase):
    def test_close_is_not_blocked_by_a_running_brightness_verification(self):
        camera = OpenCVCamera()
        capture = StaleReadbackCapture()
        camera._capture = capture

        done = threading.Event()
        thread = threading.Thread(
            target=lambda: (camera.set_brightness_percent(50), done.set()),
            daemon=True,
        )
        thread.start()
        try:
            self.assertTrue(
                capture.retry_started.wait(2.0),
                "the brightness verification never entered its retry loop",
            )
            started = time.monotonic()
            released = camera.close()
            elapsed = time.monotonic() - started
            # The retry window is ~12 x 15 ms plus the readback delays, so a
            # close() that waits for it is at least ~200 ms. close() must take
            # the service lock only for its own short critical section.
            self.assertLess(elapsed, 0.15)
            self.assertTrue(released)
            self.assertIsNone(camera.last_close_error)
            self.assertFalse(
                done.is_set(), "close() only returned after the retry budget expired"
            )
            self.assertFalse(camera.is_open)
        finally:
            done.wait(2.0)
            thread.join(2.0)

    def _wedge_the_service_lock(self, camera):
        """Occupy ``_lock`` from another thread and return a release callback."""

        holding = threading.Event()
        release = threading.Event()

        def wedged_worker():
            camera._lock.acquire()
            holding.set()
            release.wait(5.0)
            camera._lock.release()

        worker = threading.Thread(target=wedged_worker, daemon=True)
        worker.start()
        self.assertTrue(holding.wait(2.0), "could not take the service lock")
        return worker, release

    def test_close_is_bounded_when_another_thread_holds_the_service_lock(self):
        camera = OpenCVCamera()
        capture = FakeCapture()
        camera._capture = capture
        camera.device_index = 0
        worker, release = self._wedge_the_service_lock(camera)
        try:
            started = time.monotonic()
            released = camera.close(timeout=0.2)
            elapsed = time.monotonic() - started
            self.assertLess(elapsed, 1.0)
            self.assertFalse(released, "a forced teardown must be reported")
            self.assertIn("timed out", camera.last_close_error)
            # The camera must not be left half-open for the rest of the session.
            self.assertFalse(camera.is_open)
            self.assertIsNone(camera.device_index)
            self.assertEqual(capture.release_count, 1)
        finally:
            release.set()
            worker.join(2.0)
        self.assertTrue(camera.close(), "the service is usable again after the wedge")

    def test_default_close_budget_is_bounded_and_observable(self):
        self.assertGreater(CAMERA_CLOSE_TIMEOUT_SECONDS, 0.0)
        self.assertLessEqual(
            CAMERA_CLOSE_TIMEOUT_SECONDS,
            10.0,
            "teardown must never wait long enough to look like a hang",
        )

        camera = OpenCVCamera()
        camera._capture = FakeCapture()
        worker, release = self._wedge_the_service_lock(camera)
        try:
            with unittest.mock.patch.object(
                camera_service, "CAMERA_CLOSE_TIMEOUT_SECONDS", 0.2
            ):
                started = time.monotonic()
                released = camera.close()
                elapsed = time.monotonic() - started
            self.assertLess(elapsed, 1.0)
            self.assertFalse(released)
            self.assertIn("timed out", camera.last_close_error)
        finally:
            release.set()
            worker.join(2.0)

    def test_forced_close_reports_failure_and_frees_the_driver_call_in_flight(self):
        camera = OpenCVCamera()
        capture = BlockingRetryCapture()
        camera._capture = capture
        outcome = {}

        def writer():
            outcome["result"] = camera.set_property_verified(CAP_PROP_EXPOSURE, -3.0)

        def closer():
            outcome["closed"] = camera.close(timeout=0.05)

        writer_thread = threading.Thread(target=writer, daemon=True)
        writer_thread.start()
        self.assertTrue(
            capture.blocked.wait(2.0), "the retry loop never blocked in a readback"
        )

        closer_thread = threading.Thread(target=closer, daemon=True)
        closer_thread.start()
        # The worker owns the service lock for the whole readback, so an
        # unbounded close() would still be running here.
        closer_thread.join(2.0)
        self.assertFalse(
            closer_thread.is_alive(), "close() waited for the wedged worker forever"
        )
        self.assertFalse(outcome["closed"])
        self.assertFalse(camera.is_open)
        self.assertEqual(capture.release_count, 1)

        capture.unblock.set()
        writer_thread.join(2.0)
        self.assertFalse(writer_thread.is_alive())
        result = outcome["result"]
        self.assertFalse(result.supported)
        self.assertEqual(result.message, "camera closed during verification")
        self.assertEqual(camera.read(), (False, None))


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
