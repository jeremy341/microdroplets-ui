"""Small production wrapper for the official Dino-Lite DNX64 API.

The method names and signatures are based on Dino-Lite's official
``DNX64-Python-API`` and the ``DNX64.h`` supplied with the installed SDK.
Only methods required by FluidicStudio are exposed here.
"""

from __future__ import annotations

import ctypes
import os
import platform
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from .application_paths import resolve_dnx64_dll


DEFAULT_DNX64_DLL = resolve_dnx64_dll()


@dataclass(frozen=True)
class Dnx64DeviceInfo:
    index: int
    name: str
    device_id: str
    config: int


class DNX64Api:
    """Typed access to the official DNX64 DLL exports."""

    def __init__(self, dll_path: str | Path | None = None) -> None:
        self.dll_path = Path(dll_path) if dll_path is not None else resolve_dnx64_dll()
        self._dll: Any = None
        self._dll_directory: Any = None
        self.device_index: Optional[int] = None

    @property
    def is_available(self) -> bool:
        return platform.system() == "Windows" and self.dll_path.is_file()

    @property
    def is_initialized(self) -> bool:
        return self._dll is not None and self.device_index is not None

    def initialize(self, device_index: int = 0) -> None:
        if platform.system() != "Windows":
            raise RuntimeError("DNX64 is available only on Windows.")
        if not self.dll_path.is_file():
            raise FileNotFoundError(f"DNX64.dll was not found: {self.dll_path}")

        if hasattr(os, "add_dll_directory"):
            self._dll_directory = os.add_dll_directory(str(self.dll_path.parent))
        self._dll = ctypes.CDLL(str(self.dll_path))
        self._configure_signatures()
        self._dll.SetVideoDeviceIndex(int(device_index))
        if not bool(self._dll.Init()):
            self.close()
            raise RuntimeError("DNX64.Init() failed. Check the camera and SDK installation.")
        self.device_index = int(device_index)

    def _configure_signatures(self) -> None:
        signatures = {
            "Init": ([], ctypes.c_bool),
            "GetVideoDeviceCount": ([], ctypes.c_int),
            "GetVideoDeviceIndex": ([], ctypes.c_long),
            "SetVideoDeviceIndex": ([ctypes.c_int], None),
            "GetVideoDeviceName": ([ctypes.c_int], ctypes.c_wchar_p),
            "GetDeviceId": ([ctypes.c_int], ctypes.c_wchar_p),
            "GetDeviceIDA": ([ctypes.c_int], ctypes.c_char_p),
            "GetConfig": ([ctypes.c_int], ctypes.c_long),
            "GetVideoProcAmp": ([ctypes.c_long], ctypes.c_long),
            "GetVideoProcAmpValueRange": (
                [
                    ctypes.POINTER(ctypes.c_long),
                    ctypes.POINTER(ctypes.c_long),
                    ctypes.POINTER(ctypes.c_long),
                    ctypes.POINTER(ctypes.c_long),
                    ctypes.POINTER(ctypes.c_long),
                ],
                ctypes.c_long,
            ),
            "SetVideoProcAmp": ([ctypes.c_long, ctypes.c_long], None),
            "GetAutoExposure": ([ctypes.c_int], ctypes.c_long),
            "SetAutoExposure": ([ctypes.c_int, ctypes.c_long], None),
            "GetExposureValue": ([ctypes.c_int], ctypes.c_long),
            "SetExposureValue": ([ctypes.c_int, ctypes.c_long], None),
            "SetLEDState": ([ctypes.c_int, ctypes.c_long], None),
        }
        for name, (argtypes, restype) in signatures.items():
            function = getattr(self._dll, name)
            function.argtypes = argtypes
            function.restype = restype

    def _require(self):
        if not self.is_initialized:
            raise RuntimeError("DNX64 is not initialized.")
        return self._dll

    def selected_device_index(self) -> int:
        return int(self._require().GetVideoDeviceIndex())

    def enumerate_devices(self) -> list[Dnx64DeviceInfo]:
        dll = self._require()
        count = int(dll.GetVideoDeviceCount())
        devices: list[Dnx64DeviceInfo] = []
        for index in range(count):
            devices.append(
                Dnx64DeviceInfo(
                    index=index,
                    name=str(dll.GetVideoDeviceName(index) or "Dino-Lite"),
                    device_id=str(dll.GetDeviceId(index) or ""),
                    config=int(dll.GetConfig(index)),
                )
            )
        return devices

    def get_video_property_range(self, property_index: int) -> tuple[int, int, int, int]:
        property_value = ctypes.c_long(int(property_index))
        minimum = ctypes.c_long()
        maximum = ctypes.c_long()
        step = ctypes.c_long()
        default = ctypes.c_long()
        self._require().GetVideoProcAmpValueRange(
            ctypes.byref(property_value),
            ctypes.byref(minimum),
            ctypes.byref(maximum),
            ctypes.byref(step),
            ctypes.byref(default),
        )
        return minimum.value, maximum.value, step.value, default.value

    def get_brightness(self) -> int:
        return int(self._require().GetVideoProcAmp(0))

    def set_brightness(self, value: int) -> int:
        self._require().SetVideoProcAmp(0, int(value))
        return self.get_brightness()

    def get_auto_exposure(self) -> int:
        index = self.selected_device_index()
        return int(self._require().GetAutoExposure(index))

    def set_auto_exposure(self, enabled: bool) -> int:
        index = self.selected_device_index()
        self._require().SetAutoExposure(index, 1 if enabled else 0)
        return self.get_auto_exposure()

    def get_exposure(self) -> int:
        index = self.selected_device_index()
        return int(self._require().GetExposureValue(index))

    def set_exposure(self, value: int) -> int:
        index = self.selected_device_index()
        self._require().SetExposureValue(index, int(value))
        return self.get_exposure()

    def set_led(self, enabled: bool) -> None:
        """Prepared LED on/off control; no UI element is connected yet."""

        index = self.selected_device_index()
        self._require().SetLEDState(index, 1 if enabled else 0)

    def close(self) -> None:
        # DNX64 does not export a shutdown method. Dropping the ctypes object
        # releases our Python reference; OpenCV owns the preview handle.
        self.device_index = None
        self._dll = None
        if self._dll_directory is not None:
            self._dll_directory.close()
            self._dll_directory = None
