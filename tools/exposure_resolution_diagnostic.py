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


OUTPUT = Path("tools/diagnostics/exposure_resolution_diagnostic.csv")

TEST_RESOLUTIONS = [
    (1280, 1024),
    (640, 480),
]

TEST_PERCENTAGES = [0, 5, 10, 20, 50, 100]

READBACK_COUNT = 5
READBACK_INTERVAL_SECONDS = 0.25


def read_dnx_state(camera: OpenCVCamera) -> tuple[object, object]:
    if camera._dnx64 is None:
        return None, None

    try:
        auto_exposure = camera._dnx64.get_auto_exposure()
    except Exception as exc:
        print(f"Auto-exposure readback error: {exc}")
        auto_exposure = None

    try:
        exposure = camera._dnx64.get_exposure()
    except Exception as exc:
        print(f"Exposure readback error: {exc}")
        exposure = None

    return auto_exposure, exposure


def read_repeated_state(camera: OpenCVCamera) -> list[dict]:
    readings = []

    for index in range(READBACK_COUNT):
        auto_exposure, exposure = read_dnx_state(camera)

        readings.append(
            {
                "reading": index + 1,
                "auto_exposure": auto_exposure,
                "raw_exposure": exposure,
            }
        )

        print(
            f"  Readback {index + 1}: "
            f"auto={auto_exposure}, raw_exposure={exposure}"
        )

        time.sleep(READBACK_INTERVAL_SECONDS)

    return readings


def measure_fps(camera: OpenCVCamera, seconds: float = 3.0) -> float:
    start = time.monotonic()
    frames = 0

    while time.monotonic() - start < seconds:
        ok, frame = camera.read()

        if ok and frame is not None:
            frames += 1

    elapsed = time.monotonic() - start
    return frames / elapsed if elapsed > 0 else 0.0


def main() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)

    print("Searching for cameras...")
    cameras = enumerate_cameras()

    if not cameras:
        print("No cameras found.")
        return

    selected = cameras[0]

    print(
        f"Using camera: {selected.label} "
        f"(index={selected.index})"
    )

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
            print("ERROR: DNX64 is not active.")
            return

        print("DNX64 is active.")
        print(f"Hardware maximum: {HARDWARE_EXPOSURE_MAX_RAW}")
        print(f"Effective maximum: {EFFECTIVE_EXPOSURE_MAX_RAW}")

        for width, height in TEST_RESOLUTIONS:
            print()
            print("=" * 80)
            print(f"TESTING RESOLUTION: {width} x {height}")
            print("=" * 80)

            actual_width, actual_height = camera.set_resolution(
                width,
                height,
            )

            print(
                f"Requested resolution: {width} x {height}"
            )
            print(
                f"Actual resolution: "
                f"{actual_width} x {actual_height}"
            )

            # Allow the camera/DirectShow mode to settle.
            time.sleep(2.0)

            print()
            print("Re-enabling manual exposure after resolution change...")

            camera._dnx64.set_auto_exposure(False)
            camera._manual_exposure = True

            time.sleep(1.0)

            print("Manual-mode verification:")
            manual_state = read_repeated_state(camera)

            for percent in TEST_PERCENTAGES:
                print()
                print(f"SETTING EXPOSURE: {percent}%")

                requested_raw = round(
                    1
                    + (
                        EFFECTIVE_EXPOSURE_MAX_RAW - 1
                    )
                    * percent
                    / 100
                )

                print(f"Requested UI percentage: {percent}%")
                print(f"Requested raw value: {requested_raw}")

                # Set the value through the same production path.
                result = camera.set_exposure(
                    percent,
                    readback=False,
                )

                print("Repeated readback after exposure command:")
                readings = read_repeated_state(camera)

                valid_raw_values = [
                    item["raw_exposure"]
                    for item in readings
                    if isinstance(item["raw_exposure"], int)
                ]

                valid_auto_values = [
                    item["auto_exposure"]
                    for item in readings
                    if item["auto_exposure"] is not None
                ]

                final_raw = (
                    valid_raw_values[-1]
                    if valid_raw_values
                    else None
                )

                if isinstance(final_raw, int):
                    percent_of_hardware = (
                        final_raw
                        / HARDWARE_EXPOSURE_MAX_RAW
                        * 100
                    )

                    percent_of_effective = (
                        final_raw
                        / EFFECTIVE_EXPOSURE_MAX_RAW
                        * 100
                    )
                else:
                    percent_of_hardware = None
                    percent_of_effective = None

                measured_fps = measure_fps(camera)

                row = {
                    "timestamp": datetime.now().isoformat(
                        timespec="seconds"
                    ),
                    "requested_resolution": f"{width}x{height}",
                    "actual_resolution": (
                        f"{actual_width}x{actual_height}"
                    ),
                    "requested_percent": percent,
                    "requested_raw": requested_raw,
                    "result_supported": result.supported,
                    "result_message": result.message,
                    "readback_values": str(valid_raw_values),
                    "auto_exposure_values": str(valid_auto_values),
                    "final_raw_readback": final_raw,
                    "percent_of_hardware_max": (
                        percent_of_hardware
                    ),
                    "percent_of_effective_max": (
                        percent_of_effective
                    ),
                    "measured_fps": measured_fps,
                }

                rows.append(row)

                print()
                print(f"Final raw readback: {final_raw}")

                if percent_of_effective is not None:
                    print(
                        "Final percentage of effective maximum: "
                        f"{percent_of_effective:.2f}%"
                    )

                print(f"Measured FPS: {measured_fps:.2f}")
                print(f"Message: {result.message}")

    finally:
        camera.close()

    if rows:
        with OUTPUT.open(
            "w",
            newline="",
            encoding="utf-8",
        ) as file:
            writer = csv.DictWriter(
                file,
                fieldnames=rows[0].keys(),
            )
            writer.writeheader()
            writer.writerows(rows)

    print()
    print(f"Diagnostic saved to: {OUTPUT.resolve()}")


if __name__ == "__main__":
    main()