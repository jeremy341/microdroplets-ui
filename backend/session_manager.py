"""Backend-only session storage for FluidicStudio.

The manager deliberately keeps the current session in memory.  Nothing is
written to disk until :meth:`SessionManager.save` or :meth:`save_as` is
called.  Runtime objects and active hardware states are never serialized.

See ``docs/DEVELOPER_GUIDE.md`` for schema, migration, board
matching, and the deliberate separation from the waveform library.
"""

from __future__ import annotations

import copy
import json
import math
import os
import shutil
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from backend.protocol import DRIVER_AMPLITUDE_LIMITS, DRIVER_FREQUENCY_LIMITS, WAVEFORM_ALIASES, WAVEFORM_CODES


SCHEMA_VERSION = 1
APPLICATION_NAME = "FluidicStudio"
APPLICATION_VERSION = "0.1.0"

DEFAULT_FREQUENCY_HZ = 100
DEFAULT_AMPLITUDE_VPP = 100
DEFAULT_WAVEFORM = "Sinus"
DEFAULT_CALIBRATION = "water"
DEFAULT_SAMPLE_RATE_SECONDS = 1.0
VALID_WAVEFORMS = tuple(dict.fromkeys((*WAVEFORM_CODES.keys(), *WAVEFORM_ALIASES.keys())))


class SessionError(Exception):
    """Base error for invalid or unusable session data."""


class SessionValidationError(SessionError):
    """Raised when a session cannot safely be loaded or saved."""


@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class SessionLoadResult:
    session: dict[str, Any]
    path: Path
    recovered_from_backup: bool = False
    migrated: bool = False
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class SessionSaveResult:
    path: Path
    backup_path: Path | None


@dataclass(frozen=True)
class BoardMatchResult:
    profile_id: str
    status: str
    matched_device: dict[str, Any] | None = None
    reason: str = ""
    firmware_changed: bool = False


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _new_id() -> str:
    return str(uuid.uuid4())


def _finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _copy_json(value: Any) -> Any:
    """Return a JSON-compatible deep copy and reject runtime objects."""

    try:
        return json.loads(json.dumps(value, ensure_ascii=False))
    except (TypeError, ValueError) as exc:
        raise SessionValidationError("Session contains a non-serializable runtime value") from exc


def _default_pump(channel: int, driver_index: int = 0) -> dict[str, Any]:
    return {
        "channel": channel,
        "driver_index": driver_index,
        "frequency_hz": DEFAULT_FREQUENCY_HZ,
        "amplitude_vpp": DEFAULT_AMPLITUDE_VPP,
        "waveform": DEFAULT_WAVEFORM,
        # Runtime safety field.  It is normalized to False on every load.
        "enabled": False,
    }


def _default_board_profile(
    profile_id: str | None = None,
    *,
    display_name: str = "MB1",
    port: str | None = None,
) -> dict[str, Any]:
    return {
        "profile_id": profile_id or _new_id(),
        "display_name": display_name,
        "last_known_port": port,
        "device_type": "mp-Multiboard2",
        "vendor_id": None,
        "product_id": None,
        "serial_number": None,
        "last_known_firmware": None,
        "pump_configuration": {
            "channels": [_default_pump(1, 0)],
        },
        "valve_configuration": {
            "valves": [],
        },
        "sensor_configuration": {
            "selected": [],
            "calibration": DEFAULT_CALIBRATION,
            "sample_rate_seconds": DEFAULT_SAMPLE_RATE_SECONDS,
        },
    }


def create_default_session(name: str = "Unnamed Session") -> dict[str, Any]:
    """Create a new unsaved session object in memory."""

    now = _utc_now()
    return {
        "schema_version": SCHEMA_VERSION,
        "application": {
            "name": APPLICATION_NAME,
            "version": APPLICATION_VERSION,
        },
        "session": {
            "id": _new_id(),
            "name": name,
            "created_at": now,
            "updated_at": now,
        },
        "board_profiles": [],
        "ui_state": {},
        "logging": {
            "directory": "",
            "sample_rate_seconds": DEFAULT_SAMPLE_RATE_SECONDS,
        },
        "data_references": {
            "raw_files": [],
            "measurement_files": [],
        },
    }


def _normalise_runtime_safety(session: dict[str, Any]) -> dict[str, Any]:
    """Return a copy with active hardware/stream state disabled."""

    normalized = copy.deepcopy(session)
    for profile in normalized.get("board_profiles", []):
        if not isinstance(profile, dict):
            continue
        pump_configuration = profile.get("pump_configuration")
        if not isinstance(pump_configuration, dict):
            pump_configuration = {}
        for pump in pump_configuration.get("channels", []):
            if not isinstance(pump, dict):
                continue
            pump["enabled"] = False
            pump.pop("running", None)
            pump.pop("actual_hardware_state", None)
        valve_configuration = profile.get("valve_configuration")
        if not isinstance(valve_configuration, dict):
            valve_configuration = {}
        for valve in valve_configuration.get("valves", []):
            if not isinstance(valve, dict):
                continue
            valve["open"] = False
            valve["enabled"] = False
            valve.pop("actual_confirmed_state", None)
        sensor_configuration = profile.get("sensor_configuration")
        if isinstance(sensor_configuration, dict):
            sensor_configuration.pop("stream_active", None)
            sensor_configuration.pop("connection", None)
    return normalized


def _migrate_legacy_session(payload: dict[str, Any]) -> dict[str, Any]:
    """Migrate the previous sensor-only session shape when recognizable."""

    if "schema_version" in payload:
        return payload

    migrated = create_default_session()
    migrated["session"]["name"] = str(payload.get("name", "Imported Session"))
    migrated["ui_state"]["legacy_sensor_session"] = True

    # Preserve known sensor-session fields without guessing their meaning.
    for key in ("rows", "session_rows", "measurement_metadata", "log_path"):
        if key in payload:
            migrated["data_references"].setdefault("legacy", {})[key] = payload[key]
    migrated["schema_version"] = SCHEMA_VERSION
    return migrated


def migrate_session(payload: dict[str, Any]) -> tuple[dict[str, Any], bool, tuple[str, ...]]:
    if not isinstance(payload, dict):
        raise SessionValidationError("Session root must be a JSON object")

    migrated = "schema_version" not in payload
    warnings: list[str] = []
    result = _migrate_legacy_session(payload) if migrated else copy.deepcopy(payload)
    version = result.get("schema_version", 0)
    if version != SCHEMA_VERSION:
        raise SessionValidationError(f"Unsupported session schema version: {version}")
    if migrated:
        warnings.append("Legacy session format migrated in memory; save to persist the new format")
    return _normalise_runtime_safety(result), migrated, tuple(warnings)


def validate_session(session: dict[str, Any]) -> ValidationResult:
    errors: list[str] = []
    warnings: list[str] = []

    if not isinstance(session, dict):
        return ValidationResult(False, ("Session root must be an object",))
    if session.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION}")
    if not isinstance(session.get("session"), dict):
        errors.append("session metadata is missing")
    if not isinstance(session.get("board_profiles"), list):
        errors.append("board_profiles must be a list")

    profile_ids: set[str] = set()
    for index, profile in enumerate(session.get("board_profiles", [])):
        prefix = f"board_profiles[{index}]"
        if not isinstance(profile, dict):
            errors.append(f"{prefix} must be an object")
            continue
        profile_id = profile.get("profile_id")
        if not isinstance(profile_id, str) or not profile_id:
            errors.append(f"{prefix}.profile_id is required")
        elif profile_id in profile_ids:
            errors.append(f"duplicate board profile id: {profile_id}")
        else:
            profile_ids.add(profile_id)

        pump_configuration = profile.get("pump_configuration", {})
        if not isinstance(pump_configuration, dict):
            errors.append(f"{prefix}.pump_configuration must be an object")
            pump_configuration = {}
        pumps = pump_configuration.get("channels", [])
        if not isinstance(pumps, list):
            errors.append(f"{prefix}.pump_configuration.channels must be a list")
            pumps = []
        channels: set[int] = set()
        for pump_index, pump in enumerate(pumps):
            pp = f"{prefix}.pump_configuration.channels[{pump_index}]"
            if not isinstance(pump, dict):
                errors.append(f"{pp} must be an object")
                continue
            channel = pump.get("channel")
            if type(channel) is not int or not 1 <= channel <= 6:
                errors.append(f"{pp}.channel must be between 1 and 6")
            elif channel in channels:
                errors.append(f"duplicate pump channel in {prefix}: {channel}")
            else:
                channels.add(channel)
            driver_index = pump.get("driver_index", 0)
            if type(driver_index) is not int or driver_index not in DRIVER_FREQUENCY_LIMITS:
                errors.append(f"{pp}.driver_index must be 0, 1, or 2")
                driver_index = None
            frequency = pump.get("frequency_hz")
            if driver_index is not None:
                fmin, fmax = DRIVER_FREQUENCY_LIMITS[driver_index]
                if type(frequency) is not int or not fmin <= frequency <= fmax:
                    errors.append(f"{pp}.frequency_hz must be between {fmin} and {fmax}")
            amplitude = pump.get("amplitude_vpp")
            if driver_index is not None:
                amin, amax = DRIVER_AMPLITUDE_LIMITS[driver_index]
                if type(amplitude) is not int or not amin <= amplitude <= amax:
                    errors.append(f"{pp}.amplitude_vpp must be between {amin} and {amax}")
            if pump.get("waveform") not in VALID_WAVEFORMS:
                errors.append(f"{pp}.waveform is unsupported")
            if pump.get("enabled", False):
                warnings.append(f"{pp}.enabled was active and will be forced off")

        valve_configuration = profile.get("valve_configuration", {})
        if not isinstance(valve_configuration, dict):
            errors.append(f"{prefix}.valve_configuration must be an object")
            valve_configuration = {}
        valves = valve_configuration.get("valves", [])
        if not isinstance(valves, list):
            errors.append(f"{prefix}.valve_configuration.valves must be a list")

        sensor_config = profile.get("sensor_configuration", {})
        if not isinstance(sensor_config, dict):
            errors.append(f"{prefix}.sensor_configuration must be an object")
        else:
            if sensor_config.get("calibration", DEFAULT_CALIBRATION) not in {"water", "ipa"}:
                errors.append(f"{prefix}.sensor_configuration.calibration is unsupported")
            sample_rate = sensor_config.get("sample_rate_seconds", DEFAULT_SAMPLE_RATE_SECONDS)
            if not _finite_number(sample_rate) or float(sample_rate) <= 0:
                errors.append(f"{prefix}.sensor_configuration.sample_rate_seconds must be positive")

    if not isinstance(session.get("ui_state", {}), dict):
        errors.append("ui_state must be an object")

    logging = session.get("logging", {})
    if not isinstance(logging, dict):
        errors.append("logging must be an object")
    elif not _finite_number(logging.get("sample_rate_seconds", DEFAULT_SAMPLE_RATE_SECONDS)):
        errors.append("logging.sample_rate_seconds must be finite")

    return ValidationResult(not errors, tuple(errors), tuple(warnings))


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary_path.replace(path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _device_value(device: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in device:
            return device[name]
    return None


def match_board_profile(profile: dict[str, Any], device: dict[str, Any]) -> BoardMatchResult:
    """Match a saved profile to a discovered device.

    Firmware is deliberately informational: a changed firmware version is
    accepted when the physical/device metadata still matches.
    """

    profile_id = str(profile.get("profile_id", ""))
    saved_port = profile.get("last_known_port")
    current_port = _device_value(device, "port", "device", "com_port")
    if saved_port and current_port and str(saved_port).upper() != str(current_port).upper():
        return BoardMatchResult(profile_id, "port_mismatch", device, "COM port differs")

    saved_type = profile.get("device_type")
    current_type = _device_value(device, "device_type", "type")
    if saved_type and current_type and str(saved_type).casefold() != str(current_type).casefold():
        return BoardMatchResult(profile_id, "device_mismatch", device, "Device type differs")

    for profile_key, device_keys, label in (
        ("vendor_id", ("vendor_id", "vid"), "vendor ID"),
        ("product_id", ("product_id", "pid"), "product ID"),
    ):
        saved = profile.get(profile_key)
        current = _device_value(device, *device_keys)
        if saved is not None and current is not None and str(saved).casefold() != str(current).casefold():
            return BoardMatchResult(profile_id, "device_mismatch", device, f"{label} differs")

    saved_firmware = profile.get("last_known_firmware")
    current_firmware = _device_value(device, "firmware", "firmware_version")
    changed = bool(saved_firmware and current_firmware and saved_firmware != current_firmware)
    status = "firmware_changed" if changed else "matched"
    reason = "Firmware changed" if changed else "Saved device metadata matches"
    return BoardMatchResult(profile_id, status, device, reason, changed)


class SessionManager:
    """Own the in-memory session and explicit persistence operations."""

    def __init__(self, session: dict[str, Any] | None = None) -> None:
        self.current_session = _normalise_runtime_safety(
            copy.deepcopy(session) if session is not None else create_default_session()
        )
        result = validate_session(self.current_session)
        if not result.valid:
            raise SessionValidationError("; ".join(result.errors))
        self.session_path: Path | None = None
        self.is_dirty = False
        self.last_warnings: tuple[str, ...] = ()

    @property
    def has_been_saved(self) -> bool:
        return self.session_path is not None

    def mark_dirty(self) -> None:
        self.is_dirty = True

    def create_new_session(self, name: str = "Unnamed Session") -> dict[str, Any]:
        """Reset only the in-memory state; no file is deleted or written."""

        self.current_session = create_default_session(name)
        self.session_path = None
        self.is_dirty = False
        self.last_warnings = ()
        return copy.deepcopy(self.current_session)

    def discard(self) -> None:
        """Discard the RAM copy without touching the last saved file."""

        self.current_session = create_default_session()
        self.session_path = None
        self.is_dirty = False
        self.last_warnings = ()

    def load(self, path: Path | str) -> SessionLoadResult:
        path = Path(path)
        try:
            with path.open("r", encoding="utf-8") as handle:
                raw = json.load(handle)
            session, migrated, warnings = migrate_session(raw)
            validation = validate_session(session)
            if not validation.valid:
                raise SessionValidationError("; ".join(validation.errors))
            all_warnings = tuple(warnings) + validation.warnings
            self.current_session = session
            self.session_path = path
            self.is_dirty = migrated
            self.last_warnings = all_warnings
            return SessionLoadResult(session=copy.deepcopy(session), path=path, migrated=migrated, warnings=all_warnings)
        except (OSError, json.JSONDecodeError, SessionValidationError) as original_error:
            backup = Path(f"{path}.bak")
            if backup.exists():
                try:
                    with backup.open("r", encoding="utf-8") as handle:
                        raw = json.load(handle)
                    session, migrated, warnings = migrate_session(raw)
                    validation = validate_session(session)
                    if validation.valid:
                        all_warnings = (f"Loaded backup after session error: {original_error}",) + tuple(warnings)
                        self.current_session = session
                        self.session_path = path
                        self.is_dirty = True
                        self.last_warnings = all_warnings
                        return SessionLoadResult(
                            session=copy.deepcopy(session),
                            path=path,
                            recovered_from_backup=True,
                            migrated=migrated,
                            warnings=all_warnings,
                        )
                except (OSError, json.JSONDecodeError, SessionValidationError):
                    pass
            raise SessionError(f"Could not load session '{path}': {original_error}") from original_error

    def save(self, path: Path | str | None = None) -> SessionSaveResult:
        target = Path(path) if path is not None else self.session_path
        if target is None:
            raise SessionError("A path is required when saving a new session")
        return self._save_to(target)

    def save_as(self, path: Path | str) -> SessionSaveResult:
        return self._save_to(Path(path))

    def _save_to(self, target: Path) -> SessionSaveResult:
        session = _normalise_runtime_safety(copy.deepcopy(self.current_session))
        session["session"]["updated_at"] = _utc_now()
        validation = validate_session(session)
        if not validation.valid:
            raise SessionValidationError("; ".join(validation.errors))
        # Validate the serialized representation too, so runtime-only values
        # can never silently enter the file.
        serializable = _copy_json(session)
        backup_path: Path | None = None
        if target.exists():
            backup_path = Path(f"{target}.bak")
            shutil.copy2(target, backup_path)
        _write_json_atomic(target, serializable)
        self.current_session = session
        self.session_path = target
        self.is_dirty = False
        self.last_warnings = validation.warnings
        return SessionSaveResult(target, backup_path)

    def update_board_firmware(self, profile_id: str, firmware: str) -> None:
        for profile in self.current_session["board_profiles"]:
            if profile.get("profile_id") == profile_id:
                profile["last_known_firmware"] = firmware
                self.mark_dirty()
                return
        raise SessionError(f"Unknown board profile: {profile_id}")

    def match_profiles(self, discovered_devices: Iterable[dict[str, Any]]) -> list[BoardMatchResult]:
        devices = list(discovered_devices)
        results: list[BoardMatchResult] = []
        for profile in self.current_session.get("board_profiles", []):
            candidates = [match_board_profile(profile, device) for device in devices]
            matches = [result for result in candidates if result.status in {"matched", "firmware_changed"}]
            if len(matches) == 1:
                result = matches[0]
                current_firmware = _device_value(result.matched_device or {}, "firmware", "firmware_version")
                if result.firmware_changed and current_firmware:
                    self.update_board_firmware(profile["profile_id"], str(current_firmware))
                results.append(result)
            elif len(matches) > 1:
                results.append(BoardMatchResult(profile["profile_id"], "ambiguous", None, "Multiple devices match"))
            else:
                results.append(
                    BoardMatchResult(profile["profile_id"], "missing", None, "No matching connected device")
                )
        return results
