"""Persistent camera identity/mode/exposure profiles.

The current AM4113T(R9) path uses fixed mode presets rather than an FPS probing
workflow. The store still keeps legacy performance fields for compatibility,
while the active page primarily records identity, selected modes, and verified
exposure readbacks. See ``docs/DEVELOPER_GUIDE.md``.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Optional

from .fps_profiles import stable_fps
from .application_paths import CAMERA_PROFILE_PATH


FPS_LOSS_ALLOWANCE = 0.35
FPS_RETAINED_FRACTION = 1.0 - FPS_LOSS_ALLOWANCE


def default_profile_path() -> Path:
    configured = os.environ.get("FLUIDICSTUDIO_CAMERA_PROFILES")
    if configured:
        return Path(configured).expanduser()
    return CAMERA_PROFILE_PATH


class CameraProfileStore:
    """Thread-safe JSON store keyed by stable device ID and video mode."""

    def __init__(self, path: Path | None = None):
        self.path = path or default_profile_path()
        self._lock = threading.RLock()
        self._data = {"version": 2, "devices": {}}
        self._last_saved = {}
        self._load()

    @staticmethod
    def mode_key(width: int, height: int, target_fps: int) -> str:
        return f"{int(width)}x{int(height)}@{int(target_fps)}"

    def _load(self) -> None:
        with self._lock:
            try:
                text = self.path.read_text(encoding="utf-8")
            except OSError:
                return
            try:
                loaded = json.loads(text)
            except ValueError:
                # Quarantine a corrupt profile file instead of silently
                # discarding it: the loss must be diagnosable and the good
                # content recoverable from the .corrupt copy.
                try:
                    self.path.replace(self.path.with_suffix(self.path.suffix + ".corrupt"))
                except OSError:
                    pass
                return
            if isinstance(loaded, dict) and isinstance(loaded.get("devices"), dict):
                self._data = loaded
                # Version 1 profiles are intentionally kept readable.  Their
                # old baseline may be a historical peak and is re-established
                # from fresh samples the next time the mode is observed.
                self._data["version"] = max(2, int(self._data.get("version", 1) or 1))
            else:
                # Valid JSON with the wrong shape is equally unusable.
                try:
                    self.path.replace(self.path.with_suffix(self.path.suffix + ".corrupt"))
                except OSError:
                    pass

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(self._data, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        temporary.replace(self.path)

    def mode(self, device_id: str, width: int, height: int, target_fps: int) -> dict:
        with self._lock:
            return dict(
                self._data.get("devices", {})
                .get(device_id, {})
                .get("modes", {})
                .get(self.mode_key(width, height, target_fps), {})
            )

    def device(self, device_id: str) -> dict:
        """Return a detached snapshot of one device profile."""

        with self._lock:
            value = self._data.get("devices", {}).get(str(device_id), {})
            return json.loads(json.dumps(value)) if isinstance(value, dict) else {}

    def modes_for_resolution(self, device_id: str, width: int, height: int) -> list[dict]:
        """Return all stored FPS modes for one resolution, sorted by target."""

        with self._lock:
            device = self._data.get("devices", {}).get(str(device_id), {})
            modes = device.get("modes", {}) if isinstance(device, dict) else {}
            result = []
            for key, value in modes.items():
                if not isinstance(value, dict):
                    continue
                if int(value.get("width", -1)) != int(width):
                    continue
                if int(value.get("height", -1)) != int(height):
                    continue
                item = dict(value)
                item["mode_key"] = str(key)
                result.append(item)
            return sorted(result, key=lambda item: int(item.get("target_fps", 0)))

    def ensure_device(
        self,
        device_id: str,
        camera_name: str,
        *,
        hardware_min: int = 1,
        hardware_max: int = 41771,
        practical_max: int = 1045,
    ) -> dict:
        """Create or update the persistent identity part of a profile.

        This is intentionally fast: it records facts already known from the
        SDK/model profile and never probes every resolution during startup.
        """

        with self._lock:
            device = self._data.setdefault("devices", {}).setdefault(
                str(device_id), {"camera_name": camera_name, "modes": {}}
            )
            updated = {
                "camera_name": camera_name,
                "hardware_exposure_min": int(hardware_min),
                "hardware_exposure_max": int(hardware_max),
                "practical_exposure_max": int(practical_max),
            }
            if any(device.get(key) != value for key, value in updated.items()):
                device.update(updated)
                self._save()
            return dict(device)

    def ensure_mode(
        self,
        device_id: str,
        width: int,
        height: int,
        target_fps: int,
        *,
        practical_max: int = 1045,
    ) -> dict:
        """Create the per-resolution/FPS record without performing a probe."""

        with self._lock:
            device = self._data.setdefault("devices", {}).setdefault(
                str(device_id), {"camera_name": "Camera", "modes": {}}
            )
            mode = device.setdefault("modes", {}).setdefault(
                self.mode_key(width, height, target_fps), {}
            )
            updated = {
                "width": int(width),
                "height": int(height),
                "target_fps": int(target_fps),
                "practical_exposure_max": int(practical_max),
                "profile_version": 2,
            }
            if any(mode.get(key) != value for key, value in updated.items()):
                mode.update(updated)
                self._save()
            return dict(mode)

    def observe_fps(
        self,
        device_id: str,
        camera_name: str,
        width: int,
        height: int,
        target_fps: int,
        measured_fps: float,
        *,
        hardware_min: int = 1,
        hardware_max: int = 41771,
        practical_max: int = 1045,
    ) -> dict:
        """Record the best stable FPS and derive the 35%-loss threshold."""

        if measured_fps <= 0:
            return self.mode(device_id, width, height, target_fps)
        with self._lock:
            device = self._data.setdefault("devices", {}).setdefault(
                device_id,
                {"camera_name": camera_name, "modes": {}},
            )
            device["camera_name"] = camera_name
            device["hardware_exposure_min"] = int(hardware_min)
            device["hardware_exposure_max"] = int(hardware_max)
            mode = device.setdefault("modes", {}).setdefault(
                self.mode_key(width, height, target_fps),
                {},
            )
            previous = float(mode.get("baseline_fps", 0.0) or 0.0)
            samples = [
                float(value)
                for value in mode.get("fps_samples", [])
                if float(value) > 0
            ]
            if not samples:
                # Do not seed the new stable baseline from the old v1 peak.
                # Prefer the most recent real measurement and retain the old
                # value only as diagnostic legacy data.
                last_measured = float(mode.get("last_measured_fps", 0.0) or 0.0)
                if previous > 0:
                    mode["legacy_baseline_fps"] = round(previous, 2)
                if last_measured > 0:
                    samples.append(last_measured)
            samples.append(float(measured_fps))
            samples = samples[-8:]
            stable = stable_fps(samples)
            peak = max(
                [float(mode.get("peak_fps", 0.0) or 0.0), *samples]
            )
            mode_identifier = (device_id, self.mode_key(width, height, target_fps))
            now = time.monotonic()
            should_save = (
                previous <= 0.0
                or abs(stable - previous) >= 0.10
                or now - self._last_saved.get(mode_identifier, 0.0) >= 30.0
            )
            mode.update(
                {
                    "profile_version": 2,
                    "baseline_fps": stable,
                    "stable_fps": stable,
                    "peak_fps": round(peak, 2),
                    "fps_samples": [round(value, 2) for value in samples],
                    "minimum_allowed_fps": round(stable * FPS_RETAINED_FRACTION, 2),
                    "last_measured_fps": round(float(measured_fps), 2),
                    "practical_exposure_max": int(practical_max),
                }
            )
            if should_save:
                self._save()
                self._last_saved[mode_identifier] = now
            return dict(mode)

    def record_fps_measurement(
        self,
        device_id: str,
        camera_name: str,
        width: int,
        height: int,
        target_fps: int,
        measured_fps: float,
        *,
        driver_fps: float | None = None,
        usable: bool | None = None,
        exposure_percent: float | None = None,
        hardware_min: int = 1,
        hardware_max: int = 41771,
        practical_max: int = 1045,
    ) -> dict:
        """Persist a measured requested-FPS mode and its driver metadata."""

        mode = self.observe_fps(
            device_id,
            camera_name,
            width,
            height,
            target_fps,
            measured_fps,
            hardware_min=hardware_min,
            hardware_max=hardware_max,
            practical_max=practical_max,
        )
        with self._lock:
            device = self._data.setdefault("devices", {}).setdefault(
                str(device_id), {"camera_name": camera_name, "modes": {}}
            )
            mode = device.setdefault("modes", {}).setdefault(
                self.mode_key(width, height, target_fps), {}
            )
            mode.update(
                {
                    "requested_fps": int(target_fps),
                    "fps_validated": True,
                    "fps_usable": bool(usable) if usable is not None else None,
                    "driver_fps": (
                        round(float(driver_fps), 2)
                        if driver_fps is not None and float(driver_fps) > 0
                        else None
                    ),
                    "measured_at_exposure_percent": (
                        round(float(exposure_percent), 2)
                        if exposure_percent is not None
                        else None
                    ),
                    "last_fps_validation_unix": round(time.time(), 3),
                }
            )
            self._save()
            return dict(mode)

    def record_exposure_readback(
        self,
        device_id: str,
        width: int,
        height: int,
        target_fps: int,
        requested_percent: float,
        applied_percent: Optional[float],
        *,
        verified: bool,
    ) -> dict:
        """Persist the result of a real final exposure readback.

        Preview writes are intentionally not passed here.  Only a final
        release command with a verified readback updates the calibration
        record, so stale live-drag requests cannot pollute the profile.
        """

        with self._lock:
            device = self._data.setdefault(
                "devices", {}
            ).setdefault(str(device_id), {"camera_name": "Camera", "modes": {}})
            mode = device.setdefault("modes", {}).setdefault(
                self.mode_key(width, height, target_fps), {}
            )
            mode.update(
                {
                    "last_requested_exposure_percent": round(float(requested_percent), 2),
                    "last_applied_exposure_percent": (
                        round(float(applied_percent), 2)
                        if applied_percent is not None
                        else None
                    ),
                    "exposure_readback_verified": bool(verified),
                    "last_exposure_readback_unix": round(time.time(), 3),
                }
            )
            self._save()
            return dict(mode)


class CameraProfileBuilder:
    """Build one camera's profile incrementally while preview is running.

    The builder deliberately does not perform a multi-minute startup sweep.
    Identity/range data is written immediately, each selected resolution/FPS
    mode gets its own record, passive frame measurements establish its FPS
    baseline, and final exposure readbacks provide the device verification.
    """

    def __init__(
        self,
        store: CameraProfileStore,
        *,
        device_id: str,
        camera_name: str,
        hardware_min: int,
        hardware_max: int,
        practical_max: int,
    ):
        self.store = store
        self.device_id = str(device_id)
        self.camera_name = str(camera_name)
        self.hardware_min = int(hardware_min)
        self.hardware_max = int(hardware_max)
        self.practical_max = int(practical_max)
        self.store.ensure_device(
            self.device_id,
            self.camera_name,
            hardware_min=self.hardware_min,
            hardware_max=self.hardware_max,
            practical_max=self.practical_max,
        )

    def ensure_mode(self, width: int, height: int, target_fps: int) -> dict:
        return self.store.ensure_mode(
            self.device_id,
            width,
            height,
            target_fps,
            practical_max=self.practical_max,
        )

    def modes_for_resolution(self, width: int, height: int) -> list[dict]:
        return self.store.modes_for_resolution(
            self.device_id,
            width,
            height,
        )

    def observe_fps(
        self,
        width: int,
        height: int,
        target_fps: int,
        measured_fps: float,
    ) -> dict:
        return self.store.observe_fps(
            self.device_id,
            self.camera_name,
            width,
            height,
            target_fps,
            measured_fps,
            hardware_min=self.hardware_min,
            hardware_max=self.hardware_max,
            practical_max=self.practical_max,
        )

    def record_fps_measurement(
        self,
        width: int,
        height: int,
        target_fps: int,
        measured_fps: float,
        *,
        driver_fps: float | None = None,
        usable: bool | None = None,
        exposure_percent: float | None = None,
    ) -> dict:
        return self.store.record_fps_measurement(
            self.device_id,
            self.camera_name,
            width,
            height,
            target_fps,
            measured_fps,
            driver_fps=driver_fps,
            usable=usable,
            exposure_percent=exposure_percent,
            hardware_min=self.hardware_min,
            hardware_max=self.hardware_max,
            practical_max=self.practical_max,
        )

    def record_exposure_readback(
        self,
        width: int,
        height: int,
        target_fps: int,
        requested_percent: float,
        applied_percent: Optional[float],
        *,
        verified: bool,
    ) -> dict:
        return self.store.record_exposure_readback(
            self.device_id,
            width,
            height,
            target_fps,
            requested_percent,
            applied_percent,
            verified=verified,
        )
