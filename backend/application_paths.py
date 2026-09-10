"""Canonical FluidicStudio application and runtime storage locations.

FluidicStudio keeps application-owned defaults and generated user
files live below the project root.  Legacy locations are copied forward on
first start (never deleted) so an upgrade does not silently lose existing data.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
VENDOR_DIR = PROJECT_ROOT / "vendor"
DNX64_DIR = VENDOR_DIR / "dnx64"
BUNDLED_DNX64_DLL = DNX64_DIR / "DNX64.dll"
INSTALLED_DNX64_DLL = Path(r"C:\Program Files\DNX64\DNX64.dll")

# An explicit environment override is still useful for SDK development, but the
# normal application is completely self-contained under PROJECT_ROOT.
USER_DATA_DIR = Path(os.environ.get("FLUIDICSTUDIO_USER_DATA", PROJECT_ROOT / "user_data")).expanduser()
CAPTURES_DIR = USER_DATA_DIR / "captures"
SENSOR_LOGS_DIR = USER_DATA_DIR / "sensor_logs"
SESSIONS_DIR = USER_DATA_DIR / "sessions"
DIAGNOSTICS_DIR = USER_DATA_DIR / "diagnostics"
EXPORTS_DIR = USER_DATA_DIR / "exports"
ANALYTICS_DIR = USER_DATA_DIR / "analytics"
ANALYTICS_EXPORT_DIR = EXPORTS_DIR / "analytics"
ANALYTICS_SETTINGS_PATH = USER_DATA_DIR / "analytics_settings.json"
CAMERA_PROFILE_PATH = USER_DATA_DIR / "camera_profiles.json"
DEFAULT_SENSOR_LOG_PATH = SENSOR_LOGS_DIR / "log_data.csv"
WAVEFORM_LIBRARY_PATH = DATA_DIR / "waveforms.json"
DRIVER_CONFIG_PATH = DATA_DIR / "driver_config.json"

# Read-only compatibility sources from older builds. Migration copies data
# only when the portable destination does not already contain that item.
LEGACY_USER_DOCUMENTS = Path.home() / "Documents"
LEGACY_USER_DATA_DIR = LEGACY_USER_DOCUMENTS / "FluidicStudio"
LEGACY_SESSIONS_DIR = LEGACY_USER_DATA_DIR / "sessions"
LEGACY_CAMERA_PROFILE_PATH = LEGACY_USER_DATA_DIR / "camera_profiles.json"
LEGACY_SENSOR_LOG_PATH = LEGACY_USER_DOCUMENTS / "log_data.csv"
LEGACY_CAPTURES_DIR = PROJECT_ROOT / "captures"
LEGACY_DIAGNOSTICS_DIR = PROJECT_ROOT / "diagnostics"


def resolve_dnx64_dll() -> Path:
    """Return the DNX64 runtime selected for production camera control.

    Priority: explicit developer override -> bundled portable runtime -> legacy
    Program Files install.  The returned path may not exist; callers already
    handle an unavailable SDK without blocking OpenCV enumeration.
    """

    configured = os.environ.get("DNX64_DLL")
    if configured:
        return Path(configured).expanduser()

    # Use the project-local DNX64 runtime first.  This is the runtime that was
    # physically validated with the Dino-Lite AM4113T during the camera
    # control tests.  The official Program Files installation remains the
    # fallback when the bundled runtime is not present.
    if BUNDLED_DNX64_DLL.is_file():
        return BUNDLED_DNX64_DLL
    return INSTALLED_DNX64_DLL


def _copy_file_if_missing(source: Path, destination: Path) -> None:
    if not source.is_file() or destination.exists():
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy2(source, destination)
    except OSError:
        pass


def _copy_directory_contents_if_missing(source: Path, destination: Path) -> None:
    if not source.is_dir() or source.resolve() == destination.resolve():
        return
    destination.mkdir(parents=True, exist_ok=True)
    try:
        children = tuple(source.iterdir())
    except OSError:
        return
    for item in children:
        target = destination / item.name
        if target.exists():
            continue
        try:
            if item.is_dir():
                shutil.copytree(item, target)
            elif item.is_file():
                shutil.copy2(item, target)
        except OSError:
            # Migration is best-effort and must never prevent app startup.
            continue


def migrate_legacy_runtime_data() -> None:
    """Copy legacy runtime data into the portable layout without deleting it."""

    _copy_directory_contents_if_missing(LEGACY_CAPTURES_DIR, CAPTURES_DIR)
    _copy_directory_contents_if_missing(LEGACY_DIAGNOSTICS_DIR, DIAGNOSTICS_DIR)
    _copy_directory_contents_if_missing(LEGACY_SESSIONS_DIR, SESSIONS_DIR)
    _copy_file_if_missing(LEGACY_CAMERA_PROFILE_PATH, CAMERA_PROFILE_PATH)
    _copy_file_if_missing(LEGACY_SENSOR_LOG_PATH, DEFAULT_SENSOR_LOG_PATH)
    _copy_file_if_missing(
        LEGACY_SENSOR_LOG_PATH.with_name(f"{LEGACY_SENSOR_LOG_PATH.stem}_raw.csv"),
        DEFAULT_SENSOR_LOG_PATH.with_name(f"{DEFAULT_SENSOR_LOG_PATH.stem}_raw.csv"),
    )


def ensure_runtime_directories() -> None:
    """Create writable runtime directories and safely import legacy data."""

    for directory in (
        DATA_DIR,
        USER_DATA_DIR,
        CAPTURES_DIR,
        SENSOR_LOGS_DIR,
        SESSIONS_DIR,
        DIAGNOSTICS_DIR,
        EXPORTS_DIR,
        ANALYTICS_DIR,
        ANALYTICS_EXPORT_DIR,
    ):
        directory.mkdir(parents=True, exist_ok=True)
    migrate_legacy_runtime_data()
