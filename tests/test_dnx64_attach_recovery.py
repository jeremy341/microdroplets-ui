from pathlib import Path

import backend.camera_service as camera_service


class _FakeFunc:
    def __init__(self):
        self.argtypes = None
        self.restype = None


class _FakeRawDll:
    def __init__(self):
        self.SetVideoProcAmp = _FakeFunc()


class FakeVendorDNX:
    """Model an SDK where explicit Init is false but enumeration works."""

    def __init__(self, dll_path):
        self.dll_path = dll_path
        self.dnx64 = _FakeRawDll()
        self.index = None

    def SetVideoDeviceIndex(self, index):
        self.index = index

    def Init(self):
        return False

    def GetVideoDeviceCount(self):
        return 1

    def GetVideoDeviceName(self, index):
        return "Dino-Lite AM4113T"

    def GetDeviceId(self, index):
        return r"USB\VID_A168&PID_0870"

    def GetConfig(self, index):
        return 0

    def GetAutoExposure(self, index):
        return 0

    def SetAutoExposure(self, index, value):
        return None

    def GetExposureValue(self, index):
        return 500

    def SetExposureValue(self, index, value):
        return None


def test_dnx64_accepts_identity_and_control_roundtrip_even_if_first_init_bool_is_false(
    monkeypatch, tmp_path
):
    dll = tmp_path / "DNX64.dll"
    dll.write_bytes(b"placeholder")

    monkeypatch.setattr(camera_service.platform, "system", lambda: "Windows")
    monkeypatch.setattr(camera_service, "DNX64", FakeVendorDNX)
    monkeypatch.setattr(camera_service.os, "add_dll_directory", None, raising=False)

    controller = camera_service._Dnx64Controller(0, dll)
    assert controller.initialize()
    assert controller.available
    assert controller.init_returned_true is False
    assert controller.device_info(0)[0] == "Dino-Lite AM4113T"


def test_dnx64_candidate_paths_keep_v55_runtime_and_installed_fallback(monkeypatch):
    monkeypatch.delenv("DNX64_DLL", raising=False)
    paths = camera_service._dnx64_candidate_paths()
    lowered = [str(path).lower() for path in paths]
    assert any("vendor" in value and "dnx64.dll" in value for value in lowered)
    assert any("program files" in value and "dnx64.dll" in value for value in lowered)
