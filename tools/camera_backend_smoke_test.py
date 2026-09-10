"""Read-only smoke test for the production Dino-Lite camera backend."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.camera_service import OpenCVCamera, enumerate_cameras


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--camera-index", type=int, default=0)
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=1024)
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument(
        "--frame",
        default="tests/dnx64/artifacts/production_backend_frame.png",
    )
    args = parser.parse_args()

    devices = enumerate_cameras()
    camera = OpenCVCamera()
    try:
        camera.open(args.camera_index, args.width, args.height, args.fps)
        ok, frame = camera.read()
        if not ok or frame is None:
            raise RuntimeError("The production backend returned no frame.")

        import cv2

        frame_path = Path(args.frame)
        frame_path.parent.mkdir(parents=True, exist_ok=True)
        if not cv2.imwrite(str(frame_path), frame):
            raise RuntimeError(f"Could not save {frame_path}.")

        report = {
            "enumerated_devices": [asdict(device) for device in devices],
            "capabilities": asdict(camera.get_capabilities()),
            "frame": {
                "path": str(frame_path.resolve()),
                "width": int(frame.shape[1]),
                "height": int(frame.shape[0]),
            },
        }
        print(json.dumps(report, indent=2))
    finally:
        camera.close()


if __name__ == "__main__":
    main()
