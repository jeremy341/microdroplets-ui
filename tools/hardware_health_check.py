"""Portable FluidicStudio hardware/environment health check.

Default mode is passive: it checks paths, Python dependencies, serial-port
visibility and DNX64 runtime presence without starting pumps or changing camera
properties.  Use --port to perform a read-only Multiboard handshake.
"""
from __future__ import annotations

import argparse
import platform
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.application_paths import (
    BUNDLED_DNX64_DLL, CAPTURES_DIR, DIAGNOSTICS_DIR, SENSOR_LOGS_DIR,
    SESSIONS_DIR, USER_DATA_DIR, WAVEFORM_LIBRARY_PATH,
    ensure_runtime_directories, resolve_dnx64_dll,
)


def result(ok: bool, label: str, detail: str = "") -> bool:
    mark = "OK" if ok else "FAIL"
    suffix = f" - {detail}" if detail else ""
    print(f"[{mark:4}] {label}{suffix}")
    return ok


def check_serial_port(port: str) -> tuple[bool, str]:
    try:
        import serial
    except Exception as exc:
        return False, f"pyserial unavailable: {exc}"
    try:
        with serial.Serial(port, 115200, timeout=0.35, write_timeout=0.5) as ser:
            ser.reset_input_buffer()
            ser.write(b"V\n")
            ser.flush()
            deadline = time.monotonic() + 1.5
            lines=[]
            while time.monotonic() < deadline:
                raw=ser.readline()
                if raw:
                    text=raw.decode("utf-8",errors="replace").strip()
                    if text: lines.append(text)
                    if any(key in text.lower() for key in ("multiboard","version","ready","v2.")):
                        return True, text
            return (bool(lines), " | ".join(lines[-3:]) or "no reply")
    except Exception as exc:
        return False, str(exc)


def main() -> int:
    parser=argparse.ArgumentParser(description="Passive FluidicStudio hardware/environment health check")
    parser.add_argument("--port", help="Optional Multiboard COM port for a read-only firmware query")
    args=parser.parse_args()
    ensure_runtime_directories()
    checks=[]
    checks.append(result(sys.version_info >= (3,10), "Python", platform.python_version()))
    for label,path in (
        ("user_data",USER_DATA_DIR),("captures",CAPTURES_DIR),("sensor_logs",SENSOR_LOGS_DIR),
        ("sessions",SESSIONS_DIR),("diagnostics",DIAGNOSTICS_DIR),
    ):
        checks.append(result(path.is_dir(), label, str(path)))
    checks.append(result(WAVEFORM_LIBRARY_PATH.parent.is_dir(), "waveform data directory", str(WAVEFORM_LIBRARY_PATH.parent)))
    selected=resolve_dnx64_dll()
    checks.append(result(BUNDLED_DNX64_DLL.is_file(), "bundled DNX64 runtime", str(BUNDLED_DNX64_DLL)))
    checks.append(result(selected.is_file(), "selected DNX64.dll", str(selected)))
    try:
        import cv2
        checks.append(result(True, "OpenCV", getattr(cv2,"__version__","available")))
    except Exception as exc:
        checks.append(result(False, "OpenCV", str(exc)))
    try:
        import serial.tools.list_ports
        ports=[p.device for p in serial.tools.list_ports.comports()]
        checks.append(result(True, "serial support", ", ".join(ports) if ports else "no COM ports currently visible"))
    except Exception as exc:
        checks.append(result(False, "serial support", str(exc)))
    if args.port:
        ok,detail=check_serial_port(args.port)
        checks.append(result(ok, f"Multiboard {args.port}", detail))
    print("\nPassive check complete. No pump ON/amplitude commands or camera writes were issued.")
    return 0 if all(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
