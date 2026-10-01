"""Camera services combining Dino-Lite DNX64 control with OpenCV video I/O.

DNX64 is used when the official Windows SDK is installed for device identity,
brightness, exposure, and auto-exposure. OpenCV remains responsible for the
portable preview, capture, recording, resolution, and FPS path. LED support is
reported as a capability but deliberately has no visible UI control yet.

The camera stack is intentionally split between this hardware service and the
Qt workers/page in ``ui/pages/Camera.py``. See ``docs/DEVELOPER_GUIDE.md`` for thread
ownership, fixed AM4113T(R9) modes, recording timing, and DNX64 fallback.
"""

from __future__ import annotations

import math
import os
import platform
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .application_paths import resolve_dnx64_dll


# The SDK reports the physical range of the current 1.3M Premier camera as
# 1..41771. The complete range is far too bright for the microscope setup.
# Keep the hardware range for diagnostics, but expose only the practical
# 1.25-percent range requested for the microscope setup (~half of raw 2089).
HARDWARE_EXPOSURE_MAX_RAW = 41771
EFFECTIVE_EXPOSURE_FRACTION = 0.0125
EFFECTIVE_EXPOSURE_MAX_RAW = round(
    HARDWARE_EXPOSURE_MAX_RAW * EFFECTIVE_EXPOSURE_FRACTION
)

# DNX64 exposure values are numerically very wide for the microscope setup.
# A linear 0..100 slider packed almost the entire useful visual range into
# the first few percent (for example 1% was already about raw 22).  A square
# response gives fine control near the dark end while preserving the full
# validated ceiling at 100%.
EXPOSURE_SLIDER_GAMMA = 2.0


# The application is built for the Dino-Lite AM4113T(R9).  Use the camera's
# known production modes directly instead of probing or estimating frame rate.
AM4113T_VIDEO_MODES = {
    (1280, 1024): (10, 20),
    (640, 480): (10, 20, 30),
}


def am4113t_fps_options(width: int, height: int) -> tuple[int, ...]:
    return AM4113T_VIDEO_MODES.get((int(width), int(height)), ())


def documented_exposure_max(camera_name: str, device_id: Optional[str]) -> int:
    """Select the documented DNX64 range for a recognizable Dino-Lite series."""

    identity = f"{camera_name} {device_id or ''}".lower()
    if "pid_0870" in identity:
        return 41771  # AM4113T R9 / 1.3M Premier
    if "3011" in identity or "3013" in identity:
        return 30612
    if any(marker in identity for marker in ("5m", "5 mp", "5mp")):
        return 30000
    if "edge" in identity:
        return 63076
    if "premier" in identity:
        return 41771
    # DNX64 exposes no range query. Keep the verified 1.3M Premier range as a
    # conservative compatibility fallback until this device learns a profile.
    return HARDWARE_EXPOSURE_MAX_RAW


def practical_exposure_max(hardware_min: int, hardware_max: int) -> int:
    # Keep the hardware-validated five-percent ceiling. The UI mapping below
    # controls how that wide raw range is distributed across the slider.
    return max(int(hardware_min), round(int(hardware_max) * EFFECTIVE_EXPOSURE_FRACTION))


def exposure_raw_from_percent(
    percent: float, hardware_min: int, hardware_max: int
) -> int:
    """Map the UI 0..100 exposure scale to DNX64 raw exposure.

    The mapping is intentionally non-linear.  With the AM4113T/Premier range
    (1..522 practical), a linear slider would still compress the useful low end,
    so the quadratic mapping is kept for finer control. Squaring the normalized slider position
    expands that sensitive low end without removing the validated upper range.
    """

    lo = int(hardware_min)
    hi = practical_exposure_max(lo, int(hardware_max))
    if hi <= lo:
        return lo
    normalized = max(0.0, min(1.0, float(percent) / 100.0))
    curved = normalized ** EXPOSURE_SLIDER_GAMMA
    return int(round(lo + (hi - lo) * curved))


def exposure_percent_from_raw(
    raw_value: int | float, hardware_min: int, hardware_max: int
) -> float:
    """Inverse of :func:`exposure_raw_from_percent` for DNX64 readback."""

    lo = int(hardware_min)
    hi = practical_exposure_max(lo, int(hardware_max))
    if hi <= lo:
        return 0.0
    normalized = (float(raw_value) - lo) / float(hi - lo)
    normalized = max(0.0, min(1.0, normalized))
    return 100.0 * (normalized ** (1.0 / EXPOSURE_SLIDER_GAMMA))


def directshow_exposure_from_percent(percent: float) -> float:
    """Map the UI 0..100 exposure range to DirectShow's common -13..0 scale."""

    clipped = max(0.0, min(100.0, float(percent)))
    return float(round(-13.0 + (13.0 * clipped / 100.0)))

try:
    import cv2
except ImportError:  # pragma: no cover - exercised on machines without OpenCV
    cv2 = None

try:
    from .dnx64_vendor import DNX64
except ImportError:  # pragma: no cover - allows direct module execution
    DNX64 = None


@dataclass(frozen=True)
class CameraDevice:
    index: int
    label: str
    width: int
    height: int
    fps: float
    sdk_index: Optional[int] = None
    device_id: Optional[str] = None
    sdk_config: Optional[int] = None


@dataclass(frozen=True)
class CameraPropertyResult:
    """Result of a camera-property write, including driver readback."""

    supported: bool
    requested: float
    applied: Optional[float]
    message: str


@dataclass(frozen=True)
class CameraCapabilities:
    """Capabilities discovered for one connected camera."""

    camera_name: str
    device_id: Optional[str]
    connected: bool
    brightness_raw_min: int
    brightness_raw_max: int
    exposure_raw_min: int
    exposure_raw_max: int
    auto_exposure_supported: bool
    led_supported: bool
    flc_supported: bool
    supported_resolutions: tuple[tuple[int, int], ...]
    supported_fps: tuple[int, ...]


def _default_dnx64_path() -> Path:
    return resolve_dnx64_dll()


def _dnx64_candidate_paths(explicit: Optional[Path] = None) -> tuple[Path, ...]:
    """Return DNX64 runtimes in a deterministic recovery order.

    Prefer the project-local runtime as the normal first choice,
    but also try the installed SDK when the local DLL cannot initialize on the
    current Windows driver stack.  An explicit constructor path remains strict.
    """

    if explicit is not None:
        return (explicit.expanduser(),)

    project_root = Path(__file__).resolve().parents[1]
    configured = os.environ.get("DNX64_DLL")
    candidates = []
    if configured:
        candidates.append(Path(configured).expanduser())
    candidates.extend(
        [
            resolve_dnx64_dll().expanduser(),
            project_root / "vendor" / "dnx64" / "DNX64.dll",
            Path(r"C:\Program Files\DNX64\DNX64.dll"),
        ]
    )

    unique = []
    seen = set()
    for candidate in candidates:
        key = str(candidate).lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
    return tuple(unique)


class _Dnx64Controller:
    """Small production adapter around Dino-Lite's official ctypes wrapper."""

    def __init__(self, sdk_index: int = 0, dll_path: Optional[Path] = None):
        self.sdk_index = int(sdk_index)
        self._explicit_dll_path = dll_path.expanduser() if dll_path else None
        self.dll_path = (dll_path or _default_dnx64_path()).expanduser()
        self.sdk = None
        self._dll_directory = None
        self.last_error: Optional[str] = None
        self.init_returned_true: Optional[bool] = None

    @property
    def available(self) -> bool:
        return self.sdk is not None

    def _release_loaded_runtime(self) -> None:
        self.sdk = None
        if self._dll_directory is not None:
            try:
                self._dll_directory.close()
            except OSError:
                pass
            self._dll_directory = None

    def initialize(self, *, require_controls: bool = True) -> bool:
        """Attach DNX64 to the selected Dino-Lite device.

        The official Python example sets the device index and then queries
        ``GetVideoDeviceCount``.  Some SDK/runtime combinations return a false
        value from the explicit ``Init`` call even though the device is
        immediately visible afterwards, so device enumeration is the
        authoritative readiness check instead of the boolean alone.
        """

        self.last_error = None
        self.init_returned_true = None
        if DNX64 is None:
            self.last_error = "DNX64 Python wrapper is unavailable"
            return False
        if platform.system() != "Windows":
            self.last_error = "DNX64 is available only on Windows"
            return False

        errors = []
        for candidate in _dnx64_candidate_paths(self._explicit_dll_path):
            if not candidate.is_file():
                errors.append(f"{candidate}: file not found")
                continue

            self._release_loaded_runtime()
            try:
                add_dll_directory = getattr(os, "add_dll_directory", None)
                if callable(add_dll_directory):
                    self._dll_directory = add_dll_directory(str(candidate.parent))

                sdk = DNX64(str(candidate))

                sdk.SetVideoDeviceIndex(self.sdk_index)

                # Keep the explicit Init call, but do not make its boolean the only
                # proof that the SDK can address the camera.
                try:
                    self.init_returned_true = bool(sdk.Init())
                except OSError as exc:
                    self.init_returned_true = False
                    errors.append(f"{candidate}: Init raised {exc}")

                time.sleep(0.05)

                # Follow Dino-Lite's public Python usage: after selecting the
                # device index, enumerate devices. The wrapper no longer
                # performs an implicit Init inside GetVideoDeviceCount; the
                # explicit Init above owns SDK initialization.
                count = int(sdk.GetVideoDeviceCount())
                if count <= self.sdk_index:
                    raise RuntimeError(
                        f"DNX64 sees {count} video device(s), index {self.sdk_index} is unavailable"
                    )

                # A successful identity read is stronger evidence than the
                # occasionally unreliable Init boolean.
                name = sdk.GetVideoDeviceName(self.sdk_index)
                if not name:
                    raise RuntimeError("DNX64 returned an empty device name")

                if require_controls:
                    current_auto = int(sdk.GetAutoExposure(self.sdk_index))
                    if current_auto not in (0, 1):
                        raise RuntimeError(f"DNX64 returned invalid auto-exposure state {current_auto}")
                    sdk.SetAutoExposure(self.sdk_index, current_auto)
                    time.sleep(0.04)
                    if int(sdk.GetAutoExposure(self.sdk_index)) != current_auto:
                        raise RuntimeError("DNX64 auto-exposure control is not ready")
                    current_exposure = int(sdk.GetExposureValue(self.sdk_index))
                    if current_exposure < 0:
                        raise RuntimeError(f"DNX64 returned invalid exposure value {current_exposure}")
                    if current_auto == 0:
                        sdk.SetExposureValue(self.sdk_index, current_exposure)
                        time.sleep(0.04)
                        if int(sdk.GetExposureValue(self.sdk_index)) != current_exposure:
                            raise RuntimeError("DNX64 exposure control is not ready")

                self.sdk = sdk
                self.dll_path = candidate
                self.last_error = None
                return True
            except (OSError, AttributeError, TypeError, ValueError, RuntimeError) as exc:
                errors.append(f"{candidate}: {exc}")
                self._release_loaded_runtime()

        self.last_error = " | ".join(errors) if errors else "DNX64 initialization failed"
        return False

    def close(self):
        self._release_loaded_runtime()

    def device_info(self, index: int) -> tuple[str, Optional[str], int]:
        assert self.sdk is not None
        name = self.sdk.GetVideoDeviceName(index)
        device_id = self.sdk.GetDeviceId(index)
        return str(name or "Dino-Lite"), (str(device_id) if device_id else None), int(self.sdk.GetConfig(index))

    def brightness_range(self) -> tuple[int, int]:
        assert self.sdk is not None
        _index, minimum, maximum, _step, _default = self.sdk.GetVideoProcAmpValueRange(0)
        return int(minimum), int(maximum)

    def get_brightness(self) -> int:
        assert self.sdk is not None
        return int(self.sdk.GetVideoProcAmp(0))

    def set_brightness(self, value: int) -> int:
        assert self.sdk is not None
        self.sdk.SetVideoProcAmp(0, int(value))
        time.sleep(0.08)
        return self.get_brightness()

    def get_auto_exposure(self) -> bool:
        assert self.sdk is not None
        return bool(self.sdk.GetAutoExposure(self.sdk_index))

    def set_auto_exposure(self, enabled: bool, *, timeout: float = 1.5) -> None:
        assert self.sdk is not None
        self.sdk.SetAutoExposure(self.sdk_index, 1 if enabled else 0)
        if enabled:
            return
        deadline = time.monotonic() + timeout
        while int(self.sdk.GetAutoExposure(self.sdk_index)) != 0:
            if time.monotonic() >= deadline:
                raise RuntimeError("DNX64 did not confirm manual exposure mode")
            time.sleep(0.05)

    def get_exposure(self) -> int:
        assert self.sdk is not None
        return int(self.sdk.GetExposureValue(self.sdk_index))

    def set_exposure(self, value: int, *, readback: bool = True) -> int:
        assert self.sdk is not None
        self.sdk.SetExposureValue(self.sdk_index, int(value))
        if readback:
            time.sleep(0.08)
            return self.get_exposure()
        return int(value)

    def set_led(self, enabled: bool) -> None:
        assert self.sdk is not None
        self.sdk.SetLEDState(self.sdk_index, 1 if enabled else 0)

    def set_flc_level(self, level: int) -> None:
        """Set Dino-Lite Flexible LED Control level (documented range 1..6)."""

        assert self.sdk is not None
        self.sdk.SetFLCLevel(self.sdk_index, max(1, min(6, int(level))))



def _sdk_devices() -> tuple[_Dnx64Controller, list[CameraDevice]] | None:
    """Return SDK devices when DNX64 is installed; otherwise return None."""

    controller = _Dnx64Controller()
    if not controller.initialize(require_controls=False):
        return None
    try:
        count = int(controller.sdk.dnx64.GetVideoDeviceCount())
        devices = []
        for index in range(max(0, count)):
            name, device_id, config = controller.device_info(index)
            devices.append(CameraDevice(index, name, 0, 0, 0.0, index, device_id, config))
        return controller, devices
    except (OSError, AttributeError, TypeError, ValueError):
        controller.close()
        return None


def _backend():
    if cv2 is None:
        return None
    if platform.system() == "Windows" and hasattr(cv2, "CAP_DSHOW"):
        return cv2.CAP_DSHOW
    return cv2.CAP_ANY


def enumerate_cameras(
    max_devices: int = 10, *, probe_video: bool = True
) -> list[CameraDevice]:
    """Probe camera indices without leaving any device handles open."""

    if cv2 is None:
        return []

    sdk_result = _sdk_devices()
    sdk_devices = sdk_result[1] if sdk_result else []
    if sdk_result:
        sdk_result[0].close()
    if sdk_devices and not probe_video:
        # DNX64 already provides stable IDs and the complete device list. Do
        # not try to open a second DirectShow handle during hot-plug refreshes;
        # doing so can stall or disturb an active preview.
        return sdk_devices
    candidates = sdk_devices if sdk_result else [CameraDevice(i, f"Camera {i}", 0, 0, 0.0) for i in range(max_devices)]
    devices: list[CameraDevice] = []
    for candidate in candidates:
        index = candidate.index
        capture = cv2.VideoCapture(index, _backend())
        try:
            if not capture.isOpened():
                continue
            width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
            height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
            fps = float(capture.get(cv2.CAP_PROP_FPS) or 0)
            devices.append(
                CameraDevice(
                    index=index,
                    label=candidate.label,
                    width=width,
                    height=height,
                    fps=fps,
                    sdk_index=candidate.sdk_index,
                    device_id=candidate.device_id,
                    sdk_config=candidate.sdk_config,
                )
            )
        finally:
            capture.release()
    return devices


class OpenCVCamera:
    """Thread-safe OpenCV camera wrapper used by the Qt worker."""

    def __init__(self) -> None:
        self._capture = None
        self._lock = threading.RLock()
        # Serializes property verifications (which intentionally run their
        # async-apply retry outside ``_lock``) so two overlapping writes to
        # the same property cannot cross-contaminate each other's readbacks.
        self._property_verify_lock = threading.Lock()
        # The newest decoded frame belongs to the shared camera runtime rather
        # than to whichever Qt page happened to render it last. Workspace and
        # the full Camera page can therefore observe the same live frame without
        # opening a second VideoCapture or depending on page visibility.
        self._latest_frame_lock = threading.Lock()
        self._latest_frame = None
        self.device_index: Optional[int] = None
        self.sdk_index: Optional[int] = None
        self._dnx64: Optional[_Dnx64Controller] = None
        self.capabilities: Optional[CameraCapabilities] = None
        # The brightness scale cannot be inferred from a readback of zero:
        # zero is valid in both the normalized (0..1) and native (0..100)
        # driver scales. Keep the scale once it has been learned so a
        # subsequent 0 -> non-zero change does not switch representations.
        self._brightness_scale: Optional[str] = None
        self._manual_exposure: Optional[bool] = None
        self.initial_brightness_percent: Optional[int] = None
        self.initial_auto_exposure: Optional[bool] = None
        self.initial_exposure_percent: Optional[int] = None
        self.current_exposure_percent: Optional[int] = None
        self.dnx64_status: str = "not attempted"
        self.dnx64_dll_path: Optional[str] = None

    @property
    def is_open(self) -> bool:
        with self._lock:
            return self._capture is not None and self._capture.isOpened()

    def open(
        self,
        index: int,
        width: int = 1280,
        height: int = 1024,
        fps: int = 20,
        *,
        sdk_index: Optional[int] = None,
        device_name: Optional[str] = None,
        device_id: Optional[str] = None,
        sdk_config: Optional[int] = None,
    ) -> None:
        if cv2 is None:
            raise RuntimeError("OpenCV is not installed. Run: python -m pip install opencv-python")
        # The whole open sequence mutates ``_capture``/``_dnx64``/mode state;
        # hold the same lock that read()/close() use so a concurrent read
        # cannot observe the closed-and-not-yet-reopened intermediate state.
        with self._lock:
            self.close()
            capture = cv2.VideoCapture(index, _backend())
            if not capture.isOpened():
                capture.release()
                raise RuntimeError(f"Could not open camera index {index}.")
            self._capture = capture
            self.device_index = index
            self.sdk_index = index if sdk_index is None else sdk_index

            # Attach DNX64 immediately after opening
            # the DirectShow device. If that fails on a driver that needs an active
            # stream, read one frame and retry once. Controls themselves never
            # reinitialize the SDK.
            controller = _Dnx64Controller(self.sdk_index)
            if controller.initialize():
                self._dnx64 = controller
            else:
                first_error = controller.last_error
                try:
                    capture.read()
                except (AttributeError, OSError, TypeError, ValueError):
                    pass
                time.sleep(0.12)
                retry = _Dnx64Controller(self.sdk_index)
                if retry.initialize():
                    self._dnx64 = retry
                else:
                    self._dnx64 = None
                    self.dnx64_status = retry.last_error or first_error or "DNX64 unavailable"

            if self._dnx64 is not None:
                self.dnx64_status = "connected"
                self.dnx64_dll_path = str(self._dnx64.dll_path)
                try:
                    sdk_name, sdk_device_id, detected_config = self._dnx64.device_info(
                        int(self.sdk_index)
                    )
                    if not device_name or device_name.startswith("Camera "):
                        device_name = sdk_name
                    if not device_id:
                        device_id = sdk_device_id
                    if sdk_config is None:
                        sdk_config = detected_config
                except (OSError, AttributeError, TypeError, ValueError):
                    pass
            else:
                self.dnx64_dll_path = None

            self._brightness_scale = None
            self._manual_exposure = None
            self.set_video_mode(width, height, fps)
            self.capabilities = self._build_capabilities(
                device_name or f"Camera {index}", device_id, sdk_config
            )
            self._cache_initial_controls()

    def _build_capabilities(
        self, device_name: str, device_id: Optional[str], sdk_config: Optional[int]
    ) -> CameraCapabilities:
        # Do not cycle through every resolution and FPS mode here. DirectShow
        # can rebuild its capture graph for every property write, which used to
        # freeze the GUI during startup. These are the modes exposed by the
        # production UI; the selected mode is verified asynchronously by the
        # camera worker when the user changes it.
        resolutions = tuple(AM4113T_VIDEO_MODES)
        fps_values = tuple(
            sorted({fps for values in AM4113T_VIDEO_MODES.values() for fps in values})
        )
        if self._dnx64 is not None:
            try:
                minimum, maximum = self._dnx64.brightness_range()
            except (OSError, AttributeError, TypeError, ValueError):
                minimum, maximum = 0, 255
            exposure_max = documented_exposure_max(device_name, device_id)
            # SetLEDState is the normal DNX64 on/off path for Dino-Lite
            # microscopes. FLC intensity is a separate optional capability
            # advertised by bit 0x02 in the vendor SDK configuration mask.
            return CameraCapabilities(
                device_name, device_id, True, minimum, maximum, 1, exposure_max,
                True, True, bool((sdk_config or 0) & 0x02), resolutions, fps_values,
            )
        return CameraCapabilities(
            device_name, device_id, True, 0, 100, -13, 0,
            True, False, False, resolutions, fps_values,
        )

    def _cache_initial_controls(self) -> None:
        """Read startup controls in the opening thread, never in Qt's GUI thread."""

        self.initial_brightness_percent = None
        self.initial_auto_exposure = None
        self.initial_exposure_percent = None
        self.current_exposure_percent = None
        if self._dnx64 is None or self.capabilities is None:
            return
        try:
            minimum = self.capabilities.brightness_raw_min
            maximum = self.capabilities.brightness_raw_max
            raw = self._dnx64.get_brightness()
            self.initial_brightness_percent = max(
                0,
                min(100, round((raw - minimum) * 100 / max(1, maximum - minimum))),
            )
            self.initial_auto_exposure = self._dnx64.get_auto_exposure()
            exposure_min = self.capabilities.exposure_raw_min
            exposure_raw = self._dnx64.get_exposure()
            exposure_percent = round(
                exposure_percent_from_raw(
                    exposure_raw, exposure_min, self.capabilities.exposure_raw_max
                )
            )
            exposure_percent = max(0, min(100, exposure_percent))
            self.initial_exposure_percent = exposure_percent
            self.current_exposure_percent = exposure_percent
        except (OSError, AttributeError, TypeError, ValueError):
            self.initial_brightness_percent = None
            self.initial_auto_exposure = None
            self.initial_exposure_percent = None
            self.current_exposure_percent = None

    def read(self):
        with self._lock:
            if not self.is_open:
                return False, None
            return self._capture.read()

    def publish_latest_frame(self, frame) -> None:
        """Publish one worker-accepted frame to every camera view."""

        if frame is None:
            return
        with self._latest_frame_lock:
            self._latest_frame = frame

    def latest_frame(self):
        """Return the newest decoded frame without consuming it.

        ``VideoCapture.read`` returns a fresh ndarray for each successful read.
        CameraWorker publishes only frames accepted for presentation, replaces
        this reference atomically, and never mutates an older published frame.
        Multiple UI views can therefore safely observe the same frame.
        """

        with self._latest_frame_lock:
            return self._latest_frame

    def clear_latest_frame(self) -> None:
        with self._latest_frame_lock:
            self._latest_frame = None

    def set_video_mode(self, width: int, height: int, fps: int) -> tuple[int, int, float]:
        """Apply one known AM4113T(R9) mode without runtime FPS probing.

        DirectShow is asked for the exact hardware FPS after the resolution is
        applied.  CameraWorker also rate-limits frame delivery to the same
        selected value, so an ignored driver FPS request cannot make preview
        or recording exceed the chosen preset.
        """

        width = int(width)
        height = int(height)
        fps = int(fps)
        allowed = am4113t_fps_options(width, height)
        if not allowed:
            raise ValueError(f"Unsupported AM4113T resolution: {width}x{height}")
        if fps not in allowed:
            raise ValueError(
                f"Unsupported AM4113T mode: {width}x{height} @ {fps} FPS"
            )

        with self._lock:
            if not self.is_open:
                return width, height, float(fps)
            # Keep the DirectShow queue as shallow as the backend permits.
            # Unsupported CAP_PROP_BUFFERSIZE writes are harmless.
            try:
                self._capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            except (AttributeError, TypeError, ValueError):
                pass
            self._capture.set(cv2.CAP_PROP_FRAME_WIDTH, width)
            self._capture.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
            self._capture.set(cv2.CAP_PROP_FPS, fps)
            actual_width = int(self._capture.get(cv2.CAP_PROP_FRAME_WIDTH) or width)
            actual_height = int(self._capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or height)
            return actual_width, actual_height, float(fps)

    def set_resolution(self, width: int, height: int) -> tuple[int, int]:
        with self._lock:
            if self.is_open:
                self._capture.set(cv2.CAP_PROP_FRAME_WIDTH, width)
                self._capture.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
                return (
                    int(self._capture.get(cv2.CAP_PROP_FRAME_WIDTH) or width),
                    int(self._capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or height),
                )
        return width, height

    def set_fps(self, fps: int) -> float:
        """Request an FPS value from DirectShow without measuring frame arrival."""

        fps = int(fps)
        with self._lock:
            if self.is_open:
                self._capture.set(cv2.CAP_PROP_FPS, fps)
        return float(fps)

    def set_property(self, prop: int, value: float) -> bool:
        with self._lock:
            if not self.is_open:
                return False
            return bool(self._capture.set(prop, value))

    def set_property_verified(
        self, prop: int, value: float, *, tolerance: float = 1e-3
    ) -> CameraPropertyResult:
        """Write a UVC property and verify what the driver actually applied.

        The initial write and first readback happen under the service lock.
        The asynchronous-apply retry loop runs outside the lock (bounded to a
        single ``get`` per hold) so a slow DirectShow property application
        cannot block ``close()``/``stop_camera()`` for the whole retry window.
        """

        # Overlapping verifications (e.g. rapid slider changes) must not
        # cross-contaminate readbacks; serialize them without holding the
        # service lock for the retry window.
        with self._property_verify_lock:
            return self._set_property_verified_locked(prop, value, tolerance=tolerance)

    def _set_property_verified_locked(
        self, prop: int, value: float, *, tolerance: float
    ) -> CameraPropertyResult:
        with self._lock:
            if not self.is_open:
                return CameraPropertyResult(False, value, None, "camera is not open")
            capture = self._capture
            try:
                before = float(capture.get(prop))
                accepted = bool(capture.set(prop, value))
                applied = float(capture.get(prop))
            except (AttributeError, OSError, TypeError, ValueError):
                return CameraPropertyResult(False, value, None, "driver rejected the property")
        # DirectShow/UVC drivers can apply a control asynchronously. A single
        # immediate readback may therefore still contain the previous value.
        # Retry briefly in the worker thread instead of declaring a valid
        # slider write failed.
        if accepted and math.isfinite(applied):
            # DirectShow can apply exposure-mode changes after the
            # capture graph has processed another frame.  30 ms was
            # too short on the AM4113T and caused the following
            # exposure write to be sent while auto exposure was still
            # active.
            for _ in range(12):
                if abs(applied - value) <= tolerance:
                    break
                time.sleep(0.015)
                with self._lock:
                    if self._capture is not capture:
                        # The camera was closed/reopened during verification;
                        # do not read from the released capture object.
                        return CameraPropertyResult(
                            False, value, None, "camera closed during verification"
                        )
                    try:
                        applied = float(capture.get(prop))
                    except (AttributeError, OSError, TypeError, ValueError):
                        return CameraPropertyResult(False, value, None, "driver rejected the property")
        if not math.isfinite(applied) or applied < -1e8:
            return CameraPropertyResult(
                False, value, None, "property is not exposed by this camera"
            )
        if not accepted:
            return CameraPropertyResult(False, value, applied, "driver rejected the property")
        if (
            math.isfinite(before)
            and abs(value - before) > tolerance
            and abs(applied - before) <= tolerance
        ):
            if accepted:
                # The capture backend accepted the write, but its
                # readback is still one update behind. Treat this as a
                # pending asynchronous application rather than rejecting
                # the user's slider change.
                return CameraPropertyResult(
                    True, value, applied, "accepted; readback pending"
                )
            return CameraPropertyResult(
                False, value, applied, "driver ignored the requested value"
            )
        # UVC drivers may quantize or clamp values. That is supported; a
        # sentinel readback such as -1 means the property is unavailable.
        return CameraPropertyResult(True, value, applied, "applied")

    def set_auto_exposure(self, enabled: bool) -> CameraPropertyResult:
        """Switch the UVC exposure mode and wait for the driver to settle.

        OpenCV's DirectShow backend convention is 0.75 for automatic and
        0.25 for manual exposure.  The camera driver may expose the mode as
        a slightly different value, so the write is verified rather than
        treating ``VideoCapture.set`` as proof of success.
        """

        if self._dnx64 is not None:
            try:
                self._dnx64.set_auto_exposure(enabled)
                self._manual_exposure = not enabled
                if not enabled and self.capabilities is not None:
                    raw_min = self.capabilities.exposure_raw_min
                    frozen_raw = self._dnx64.get_exposure()
                    frozen_percent = round(
                        exposure_percent_from_raw(
                            frozen_raw, raw_min, self.capabilities.exposure_raw_max
                        )
                    )
                    self.current_exposure_percent = max(0, min(100, frozen_percent))
                return CameraPropertyResult(True, float(enabled), float(enabled), "applied through DNX64")
            except (OSError, AttributeError, TypeError, ValueError) as exc:
                return CameraPropertyResult(False, float(enabled), None, f"DNX64 auto-exposure failed: {exc}")
        if cv2 is None:
            return CameraPropertyResult(
                False, float(bool(enabled)), None, "OpenCV is not installed"
            )

        target = 0.75 if enabled else 0.25
        result = self.set_property_verified(
            cv2.CAP_PROP_AUTO_EXPOSURE,
            target,
            tolerance=0.02,
        )
        if result.supported:
            self._manual_exposure = not enabled
        return result

    def set_exposure(self, value: float, *, readback: bool = True) -> CameraPropertyResult:
        """Apply manual exposure after confirming that auto mode is off."""

        if self._dnx64 is not None:
            try:
                percent = max(0.0, min(100.0, float(value)))
                raw_min = self.capabilities.exposure_raw_min if self.capabilities else 1
                hardware_max = (
                    self.capabilities.exposure_raw_max
                    if self.capabilities
                    else HARDWARE_EXPOSURE_MAX_RAW
                )
                raw_value = exposure_raw_from_percent(percent, raw_min, hardware_max)
                if self._manual_exposure is not True:
                    self._dnx64.set_auto_exposure(False)
                    self._manual_exposure = True
                applied_raw = self._dnx64.set_exposure(raw_value, readback=readback)
                applied_percent = exposure_percent_from_raw(
                    applied_raw, raw_min, hardware_max
                )
                applied_percent = max(0.0, min(100.0, applied_percent))
                self.current_exposure_percent = round(applied_percent)
                return CameraPropertyResult(True, percent, applied_percent, "applied through DNX64")
            except (OSError, AttributeError, TypeError, ValueError) as exc:
                return CameraPropertyResult(False, value, None, f"DNX64 exposure failed: {exc}")
        if cv2 is None:
            return CameraPropertyResult(False, value, None, "OpenCV is not installed")
        with self._lock:
            if not self.is_open:
                return CameraPropertyResult(False, value, None, "camera is not open")

        if self._manual_exposure is not True:
            mode = self.set_auto_exposure(False)
            if not mode.supported:
                return CameraPropertyResult(
                    False, value, mode.applied, "manual exposure could not be enabled"
                )

        # DNX64 is the normal Dino-Lite path. If the SDK is unavailable,
        # translate the UI percentage to DirectShow's common logarithmic
        # exposure range instead of writing a literal 0..100 value.
        percent = max(0.0, min(100.0, float(value)))
        directshow_value = directshow_exposure_from_percent(percent)
        result = self.set_property_verified(
            cv2.CAP_PROP_EXPOSURE, directshow_value, tolerance=0.55
        )
        if result.message == "accepted; readback pending":
            time.sleep(0.08)
            result = self.set_property_verified(
                cv2.CAP_PROP_EXPOSURE, directshow_value, tolerance=0.55
            )
        return CameraPropertyResult(
            result.supported,
            percent,
            percent if result.supported else result.applied,
            "applied through DirectShow fallback"
            if result.supported
            else result.message,
        )

    def get_property(self, prop: int, default: float = 0.0) -> float:
        with self._lock:
            if not self.is_open:
                return default
            try:
                value = float(self._capture.get(prop))
            except (AttributeError, TypeError, ValueError):
                return default
            return value if math.isfinite(value) else default

    def set_brightness_percent(self, percent: float) -> CameraPropertyResult:
        """Set the UI's 0..100 brightness value in the driver's native scale.

        OpenCV does not normalize this property consistently. DirectShow
        devices may expose either 0..1 or 0..100. Do not optimistically write
        0.89 and call it successful: on a 0..100 driver that is a genuinely
        very dark brightness value. The current readback tells us which scale
        this capture exposes, so the write uses that scale consistently.
        """

        if self._dnx64 is not None:
            try:
                minimum, maximum = self._dnx64.brightness_range()
                requested = max(0.0, min(100.0, float(percent)))
                raw_value = round(minimum + (maximum - minimum) * requested / 100.0)
                applied_raw = self._dnx64.set_brightness(raw_value)
                applied = (applied_raw - minimum) * 100.0 / max(1, maximum - minimum)
                return CameraPropertyResult(True, requested, max(0.0, min(100.0, applied)), "applied through DNX64")
            except (OSError, AttributeError, TypeError, ValueError) as exc:
                return CameraPropertyResult(False, percent, None, f"DNX64 brightness failed: {exc}")
        if cv2 is None:
            return CameraPropertyResult(False, percent, None, "OpenCV is not installed")
        with self._lock:
            if not self.is_open:
                return CameraPropertyResult(False, percent, None, "camera is not open")
            try:
                current = float(self._capture.get(cv2.CAP_PROP_BRIGHTNESS))
            except (AttributeError, OSError, TypeError, ValueError):
                return CameraPropertyResult(False, percent, None, "brightness is not exposed")
            if not math.isfinite(current) or current < -1e8:
                return CameraPropertyResult(False, percent, None, "brightness is not exposed")

            brightness = max(0.0, min(100.0, float(percent)))

            # A non-zero readback tells us the scale on the first call. A
            # zero readback does not, so retain the scale learned previously.
            if self._brightness_scale is None and abs(current) > 1.5:
                self._brightness_scale = "percent"
            # Values in 0..1 are ambiguous: they can be a normalized driver
            # value or a low value on a native 0..100 driver. Do not infer a
            # scale from that readback alone.

            if self._brightness_scale == "percent":
                result = self.set_property_verified(
                    cv2.CAP_PROP_BRIGHTNESS,
                    brightness,
                    tolerance=0.5,
                )
            elif self._brightness_scale == "normalized":
                result = self.set_property_verified(
                    cv2.CAP_PROP_BRIGHTNESS,
                    brightness / 100.0,
                    tolerance=0.005,
                )
            else:
                # No scale can be learned when the camera starts at exactly
                # zero. Try the common native DirectShow range first. If the
                # driver clamps the readback to <=1, remember normalized
                # scale and retry using the normalized representation.
                result = self.set_property_verified(
                    cv2.CAP_PROP_BRIGHTNESS,
                    brightness,
                    # Use a tight tolerance while the scale is unknown. A
                    # broad percent-scale tolerance can mistake a real
                    # normalized change such as 0.5 -> 1.0 for a stale
                    # readback and leave the scale unresolved.
                    tolerance=0.01,
                )
                if result.supported and result.applied is not None:
                    # A delayed readback is deliberately not used to learn
                    # the scale. The old value may be zero (or another value
                    # from the previous request), so recording a scale here
                    # could make all following slider values use the wrong
                    # representation. Retry the native write on the next
                    # request until the driver exposes an unambiguous value.
                    if result.message == "accepted; readback pending":
                        self._brightness_scale = None
                    elif brightness <= 1.0:
                        # A request of 0..1 does not distinguish the two
                        # possible driver representations. Keep the scale
                        # unknown until a larger request provides evidence.
                        self._brightness_scale = None
                    elif result.applied <= 1.5:
                        # The driver accepted a value above the normalized
                        # range but read back a value within 0..1: it is a
                        # normalized UVC property.  The first native write
                        # was only a scale probe; do not leave the camera at
                        # the clamped value (for example 1.0 when the user
                        # requested 89%).  Re-issue the request in the
                        # driver's actual scale and return that verified
                        # result to the caller.
                        self._brightness_scale = "normalized"
                        result = self.set_property_verified(
                            cv2.CAP_PROP_BRIGHTNESS,
                            brightness / 100.0,
                            tolerance=0.005,
                        )
                    else:
                        # The driver read back the requested native value (or
                        # a native value in the 0..100 range).
                        self._brightness_scale = "percent"
                elif brightness > 0.0:
                    # Only fall back to 0..1 when the native write was
                    # actually rejected. A delayed readback is not proof of
                    # a wrong scale and must not make the control one-way.
                    native_accepted = result.message != "driver rejected the property"
                    if not native_accepted:
                        self._brightness_scale = "normalized"
                        result = self.set_property_verified(
                            cv2.CAP_PROP_BRIGHTNESS,
                            brightness / 100.0,
                            tolerance=0.005,
                        )
                    else:
                        self._brightness_scale = "percent"

            return result

    def get_capabilities(self) -> Optional[CameraCapabilities]:
        """Return the immutable profile discovered while opening the camera."""

        return self.capabilities

    def set_led_enabled(self, enabled: bool) -> CameraPropertyResult:
        """Switch the microscope LEDs through the official DNX64 SDK."""

        if self._dnx64 is None:
            return CameraPropertyResult(False, float(enabled), None, "LED control requires DNX64")
        try:
            self._dnx64.set_led(bool(enabled))
            return CameraPropertyResult(
                True,
                float(enabled),
                float(enabled),
                "applied through DNX64",
            )
        except (OSError, AttributeError, TypeError, ValueError) as exc:
            return CameraPropertyResult(False, float(enabled), None, f"DNX64 LED failed: {exc}")

    def set_led_intensity_percent(self, percent: float) -> CameraPropertyResult:
        """Set optional Dino-Lite FLC intensity using its six hardware levels.

        The vendor SDK exposes FLC as integer levels 1..6 rather than a true
        0..100 control. The UI uses a percentage because it is easier to read;
        the backend snaps that percentage to the closest documented level.
        """

        requested = max(0.0, min(100.0, float(percent)))
        capabilities = self.capabilities
        if (
            self._dnx64 is None
            or capabilities is None
            or not capabilities.flc_supported
        ):
            return CameraPropertyResult(
                False, requested, None, "LED intensity requires DNX64 FLC support"
            )
        level = max(1, min(6, int(round((requested / 100.0) * 5.0)) + 1))
        applied_percent = float((level - 1) * 20)
        try:
            self._dnx64.set_flc_level(level)
            return CameraPropertyResult(
                True, requested, applied_percent, "applied through DNX64 FLC"
            )
        except (OSError, AttributeError, TypeError, ValueError) as exc:
            return CameraPropertyResult(
                False, requested, None, f"DNX64 LED intensity failed: {exc}"
            )

    def close(self) -> None:
        with self._lock:
            if self._capture is not None:
                self._capture.release()
            self._capture = None
            self.device_index = None
            self.sdk_index = None
            self.dnx64_status = "not attempted"
            self.dnx64_dll_path = None
            if self._dnx64 is not None:
                self._dnx64.close()
                self._dnx64 = None
            self.capabilities = None
            self._brightness_scale = None
            self._manual_exposure = None
            self.initial_brightness_percent = None
            self.initial_auto_exposure = None
        self.clear_latest_frame()
