"""OpenCV and optional Dino-Lite SDK camera services.

The AM4113T is exposed to Windows as a USB video device. OpenCV handles the
portable preview/capture path. If the vendor DNX64 Python package and DLLs are
installed separately, :class:`DinoLiteSdkBridge` exposes the vendor-specific
controls without making the application depend on proprietary files.
"""

from __future__ import annotations

import importlib
import os
import platform
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

try:
    import cv2
except ImportError:  # pragma: no cover - exercised on machines without OpenCV
    cv2 = None


@dataclass(frozen=True)
class CameraDevice:
    index: int
    label: str
    width: int
    height: int
    fps: float


def _backend():
    if cv2 is None:
        return None
    if platform.system() == "Windows" and hasattr(cv2, "CAP_DSHOW"):
        return cv2.CAP_DSHOW
    return cv2.CAP_ANY


def enumerate_cameras(max_devices: int = 10) -> list[CameraDevice]:
    """Probe camera indices without leaving any device handles open."""

    if cv2 is None:
        return []

    devices: list[CameraDevice] = []
    for index in range(max_devices):
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
                    label=f"Camera {index}",
                    width=width,
                    height=height,
                    fps=fps,
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
        self.device_index: Optional[int] = None

    @property
    def is_open(self) -> bool:
        with self._lock:
            return self._capture is not None and self._capture.isOpened()

    def open(self, index: int, width: int = 1280, height: int = 1024, fps: int = 30) -> None:
        if cv2 is None:
            raise RuntimeError("OpenCV is not installed. Run: python -m pip install opencv-python")
        self.close()
        capture = cv2.VideoCapture(index, _backend())
        if not capture.isOpened():
            capture.release()
            raise RuntimeError(f"Could not open camera index {index}.")
        self._capture = capture
        self.device_index = index
        self.set_resolution(width, height)
        self.set_fps(fps)

    def read(self):
        with self._lock:
            if not self.is_open:
                return False, None
            return self._capture.read()

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
        with self._lock:
            if self.is_open:
                self._capture.set(cv2.CAP_PROP_FPS, fps)
                return float(self._capture.get(cv2.CAP_PROP_FPS) or fps)
        return float(fps)

    def set_property(self, prop: int, value: float) -> bool:
        with self._lock:
            if not self.is_open:
                return False
            return bool(self._capture.set(prop, value))

    def get_property(self, prop: int, default: float = 0.0) -> float:
        with self._lock:
            if not self.is_open:
                return default
            return float(self._capture.get(prop) or default)

    def close(self) -> None:
        with self._lock:
            if self._capture is not None:
                self._capture.release()
            self._capture = None
            self.device_index = None


class DinoLiteSdkBridge:
    """Best-effort bridge for the separately installed official DNX64 SDK.

    The official Python wrapper is optional and requires DNX64.dll, DNX32.dll
    and libusbK.dll. Unknown SDK versions are never allowed to break OpenCV.
    """

    def __init__(self) -> None:
        self._sdk = None
        self.available = False
        try:
            module = importlib.import_module("DNX64")
            sdk_class = getattr(module, "DNX64")
            sdk_dir = Path(os.environ.get("DINO_LITE_SDK_DIR", "."))
            dll = os.environ.get("DINO_LITE_SDK_DLL", str(sdk_dir / "DNX64.dll"))
            if Path(dll).exists():
                self._sdk = sdk_class(dll)
                self.available = True
        except (ImportError, OSError, RuntimeError, TypeError):
            self._sdk = None

    def set_device_index(self, index: int) -> bool:
        return self._call("SetVideoDeviceIndex", index) is not None

    def set_auto_exposure(self, enabled: bool) -> bool:
        if not self.available:
            return False
        method = getattr(self._sdk, "SetAETarget", None)
        if method is None:
            return False
        try:
            method(0, 100 if enabled else 0)
            return True
        except (OSError, RuntimeError, TypeError):
            return False

    def set_exposure(self, value: int) -> bool:
        return self._call("SetExposureValue", 0, value) is not None

    def set_led_enabled(self, enabled: bool) -> bool:
        for name in ("SetLED", "SetLed", "SetLEDState", "SetLight"):
            result = self._call(name, 0, int(enabled))
            if result is not None:
                return True
        return False

    def trigger_microtouch(self) -> bool:
        for name in ("Capture", "CaptureImage", "TriggerCapture", "DoCapture"):
            result = self._call(name)
            if result is not None:
                return True
        return False

    def _call(self, name: str, *args):
        if not self.available:
            return None
        method = getattr(self._sdk, name, None)
        if method is None:
            return None
        try:
            return method(*args)
        except (OSError, RuntimeError, TypeError):
            return None
