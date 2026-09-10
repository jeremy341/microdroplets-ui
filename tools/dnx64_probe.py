"""Small Windows probe for Dino-Lite DNX64 attachment.

Run from the project root with:
    python tools/dnx64_probe.py

The probe is read-only: it does not change LED or exposure values.
"""
from __future__ import annotations

import os
import platform
import struct
from pathlib import Path

from backend.camera_service import _Dnx64Controller, _dnx64_candidate_paths


def main() -> int:
    print("FluidicStudio DNX64 probe")
    print(f"OS: {platform.platform()}")
    print(f"Python architecture: {struct.calcsize('P') * 8}-bit")
    print(f"DNX64_DLL override: {os.environ.get('DNX64_DLL') or '<not set>'}")
    print("Runtime candidates:")
    for path in _dnx64_candidate_paths():
        print(f"  - {path} [{'exists' if Path(path).is_file() else 'missing'}]")

    if platform.system() != "Windows":
        print("DNX64 is Windows-only.")
        return 2

    controller = _Dnx64Controller(0)
    ok = controller.initialize()
    print(f"Attach: {'OK' if ok else 'FAILED'}")
    print(f"Explicit Init returned true: {controller.init_returned_true}")
    print(f"Selected DLL: {controller.dll_path if ok else '<none>'}")
    if not ok:
        print(f"Reason: {controller.last_error}")
        return 1

    try:
        name, device_id, config = controller.device_info(0)
        print(f"Device 0 name: {name}")
        print(f"Device 0 ID: {device_id}")
        print(f"Device 0 config: {config}")
        try:
            print(f"Auto exposure: {controller.get_auto_exposure()}")
            print(f"Exposure raw: {controller.get_exposure()}")
        except Exception as exc:
            print(f"Control readback failed: {exc}")
    finally:
        controller.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
