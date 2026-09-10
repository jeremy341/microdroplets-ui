from __future__ import annotations

import csv
import time
from datetime import datetime
from pathlib import Path

from backend.camera_service import (
    EFFECTIVE_EXPOSURE_MAX_RAW,
    HARDWARE_EXPOSURE_MAX_RAW,
    OpenCVCamera,
    enumerate_cameras,
)


OUTPUT = Path("diagnostics/exposure_diagnostic.csv")
TEST_RESOLUTIONS = [
    (1280, 1024),
    (640, 480),
]
TEST_PERCENTAGES = [0, 5, 10, 20, 50, 100]
WARMUP_SECONDS = 2
MEASURE_SECONDS = 3


def measure_frames(camera: OpenCVCamera, seconds: float) -> tuple[int, int, float]:
    start = time.monotonic()
    frames = 0
    failed_reads = 0
    read_times = []

    while time.monotonic() - start < seconds:
        read_start = time.perf_counter()
        ok, frame = camera.read()
        read_times.append((time.perf_counter() - read_start) * 1000)

        if ok and frame is not None:
            frames += 1
        else:
            failed_reads += 1

    elapsed = time.monotonic() - start
    fps = frames / elapsed if elapsed > 0 else 0
    average_read_ms = sum(read_times) / len(read_times) if read_times else 0

    return frames, failed_reads, average_read_ms, fps


def read_camera_state(camera: OpenCVCamera) -> dict:
    state = {
        "actual_width": "",
        "actual_height": "",
        "actual_fps": "",
        "opencv_exposure": "",
        "opencv_auto_exposure": "",
        "dnx_raw_exposure": "",
        "dnx_auto_exposure": "",
    }

    capture = camera._capture

    if capture is not None:
        try:
            import cv2

            state["actual_width"] = int(
                capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0
            )
            state["actual_height"] = int(
                capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0
            )
            state["actual_fps"] = float(
                capture.get(cv2.CAP_PROP_FPS) or 0
            )
            state["opencv_exposure"] = float(
                capture.get(cv2.CAP_PROP_EXPOSURE)
            )
            state["opencv_auto_exposure"] = float(
                capture.get(cv2.CAP_PROP_AUTO_EXPOSURE)
            )
        except Exception as exc:
            print(f"OpenCV readback error: {exc}")

    if camera._dnx64 is not None:
        try:
            state["dnx_raw_exposure"] = camera._dnx64.get_exposure()
            state["dnx_auto_exposure"] = camera._dnx64.get_auto_exposure()
        except Exception as exc:
            print(f"DNX64 readback error: {exc}")

    return state


def main() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)

    print("Searching for cameras...")
    cameras = enumerate_cameras()

    if not cameras:
        print("No cameras found.")
        return

    for camera_info in cameras:
        print(
            f"Found camera: index={camera_info.index}, "
            f"name={camera_info.label}, "
            f"sdk_index={camera_info.sdk_index}, "
            f"device_id={camera_info.device_id}"
        )

    selected = cameras[0]

    print()
    print(f"Using camera index: {selected.index}")
    print(f"Hardware exposure maximum: {HARDWARE_EXPOSURE_MAX_RAW}")
    print(f"Effective exposure maximum: {EFFECTIVE_EXPOSURE_MAX_RAW}")
    print()

    camera = OpenCVCamera()

    rows = []

    try:
        camera.open(
            selected.index,
            1280,
            1024,
            30,
            sdk_index=selected.sdk_index,
            device_name=selected.label,
            device_id=selected.device_id,
            sdk_config=selected.sdk_config,
        )

        if camera._dnx64 is None:
            print("WARNING: DNX64 is not active.")
            print("Raw exposure diagnostics will not be available.")
        else:
            print("DNX64 is active.")

        # Ensure manual exposure mode.
        if camera._dnx64 is not None:
            print("Switching to manual exposure...")
            camera._dnx64.set_auto_exposure(False)
            camera._manual_exposure = True

        for requested_width, requested_height in TEST_RESOLUTIONS:
            print()
            print("=" * 80)
            print(f"TESTING RESOLUTION: {requested_width} x {requested_height}")
            print("=" * 80)

            actual_width, actual_height = camera.set_resolution(
                requested_width,
                requested_height,
            )

            print(
                f"Requested resolution: {requested_width} x {requested_height}"
            )
            print(f"Actual resolution: {actual_width} x {actual_height}")

            # Let DirectShow/camera mode settle.
            time.sleep(2)

            for percent in TEST_PERCENTAGES:
                print()
                print(f"Setting exposure to {percent}%...")

                started = time.perf_counter()
                result = camera.set_exposure(
                    percent,
                    readback=True,
                )
                command_ms = (time.perf_counter() - started) * 1000

                # Let the camera apply the new value.
                time.sleep(0.5)

                state = read_camera_state(camera)

                frames, failed_reads, average_read_ms, measured_fps = (
                    measure_frames(camera, MEASURE_SECONDS)
                )

                requested_raw = round(
                    1
                    + (EFFECTIVE_EXPOSURE_MAX_RAW - 1)
                    * percent
                    / 100
                )

                applied_raw = state["dnx_raw_exposure"]

                if isinstance(applied_raw, int):
                    raw_percentage_of_hardware = (
                        applied_raw / HARDWARE_EXPOSURE_MAX_RAW * 100
                    )
                    raw_percentage_of_effective = (
                        (applied_raw - 1)
                        / max(1, EFFECTIVE_EXPOSURE_MAX_RAW - 1)
                        * 100
                    )
                else:
                    raw_percentage_of_hardware = ""
                    raw_percentage_of_effective = ""

                row = {
                    "timestamp": datetime.now().isoformat(timespec="seconds"),
                    "requested_resolution": (
                        f"{requested_width}x{requested_height}"
                    ),
                    "actual_resolution": (
                        f"{state['actual_width']}x{state['actual_height']}"
                    ),
                    "requested_percent": percent,
                    "requested_raw": requested_raw,
                    "result_supported": result.supported,
                    "result_applied_percent": result.applied,
                    "result_message": result.message,
                    "dnx_raw_readback": applied_raw,
                    "dnx_percent_of_hardware_max": (
                        raw_percentage_of_hardware
                    ),
                    "dnx_percent_of_effective_max": (
                        raw_percentage_of_effective
                    ),
                    "dnx_auto_exposure": state["dnx_auto_exposure"],
                    "opencv_exposure": state["opencv_exposure"],
                    "opencv_auto_exposure": (
                        state["opencv_auto_exposure"]
                    ),
                    "actual_fps": state["actual_fps"],
                    "measured_fps": measured_fps,
                    "frames": frames,
                    "failed_reads": failed_reads,
                    "average_read_ms": average_read_ms,
                    "exposure_command_ms": command_ms,
                }

                rows.append(row)

                print(
                    f"requested UI:       {percent}%"
                )
                print(
                    f"requested raw:      {requested_raw}"
                )
                print(
                    f"DNX raw readback:   {applied_raw}"
                )
                print(
                    f"result applied:     {result.applied}%"
                )
                print(
                    f"hardware max:       "
                    f"{raw_percentage_of_hardware:.2f}%"
                    if raw_percentage_of_hardware != ""
                    else "hardware max:       unavailable"
                )
                print(
                    f"effective max:      "
                    f"{raw_percentage_of_effective:.2f}%"
                    if raw_percentage_of_effective != ""
                    else "effective max:      unavailable"
                )
                print(
                    f"actual resolution:  "
                    f"{state['actual_width']}x{state['actual_height']}"
                )
                print(
                    f"measured FPS:       {measured_fps:.2f}"
                )
                print(
                    f"failed frame reads:  {failed_reads}"
                )
                print(
                    f"average read time:  {average_read_ms:.2f} ms"
                )
                print(
                    f"command duration:    {command_ms:.2f} ms"
                )
                print(
                    f"message:            {result.message}"
                )

    finally:
        camera.close()

    if rows:
        with OUTPUT.open("w", newline="", encoding="utf-8") as file:
            writer = csv.DictWriter(file, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)

        print()
        print(f"Diagnostic results saved to: {OUTPUT.resolve()}")


if __name__ == "__main__":
    main()