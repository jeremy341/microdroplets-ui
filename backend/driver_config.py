"""Static pump-driver configuration loaded once per FluidicStudio process.

The Multiboard layout itself is fixed for this project:
* CH1-CH4 use an mp-Highdriver4.
* CH5 can use either an mp-Lowdriver or mp-Highdriver.
* CH6 uses an mp-Driver.

Only the CH5 choice is expected to be changed by a normal lab user.  The JSON
configuration describes the hardware that is physically installed; it is not
an automatic detector and changing it requires an application restart.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

from backend.application_paths import DRIVER_CONFIG_PATH


SCHEMA_VERSION = 1
DEFAULT_DRIVERS = {
    "ch1_4": "highdriver4",
    "ch5": "lowdriver",
    "ch6": "mp_driver",
}
ALLOWED_DRIVER_VALUES = {
    "ch1_4": frozenset(("highdriver4",)),
    "ch5": frozenset(("lowdriver", "highdriver")),
    "ch6": frozenset(("mp_driver",)),
}


class DriverConfigError(ValueError):
    """Raised when ``data/driver_config.json`` is malformed or unsupported."""


@dataclass(frozen=True)
class DriverConfiguration:
    schema_version: int = SCHEMA_VERSION
    ch1_4: str = "highdriver4"
    ch5: str = "lowdriver"
    ch6: str = "mp_driver"

    def driver_for_group(self, group: str) -> str:
        try:
            return {
                "ch1_4": self.ch1_4,
                "ch5": self.ch5,
                "ch6": self.ch6,
            }[group]
        except KeyError as exc:
            raise ValueError(f"Unknown driver group: {group}") from exc


_cached_config: DriverConfiguration | None = None


def _validate(raw: object) -> DriverConfiguration:
    if not isinstance(raw, dict):
        raise DriverConfigError("Driver configuration must be a JSON object.")

    schema_version = raw.get("schema_version")
    if schema_version != SCHEMA_VERSION:
        raise DriverConfigError(
            f"Unsupported driver configuration schema_version {schema_version!r}; "
            f"expected {SCHEMA_VERSION}."
        )

    drivers = raw.get("drivers")
    if not isinstance(drivers, dict):
        raise DriverConfigError("Driver configuration must contain a 'drivers' object.")

    missing = [key for key in ALLOWED_DRIVER_VALUES if key not in drivers]
    if missing:
        raise DriverConfigError(
            "Driver configuration is missing: " + ", ".join(sorted(missing))
        )

    unexpected = sorted(set(drivers) - set(ALLOWED_DRIVER_VALUES))
    if unexpected:
        raise DriverConfigError(
            "Unsupported driver configuration key(s): " + ", ".join(unexpected)
        )

    normalized: dict[str, str] = {}
    for group, allowed in ALLOWED_DRIVER_VALUES.items():
        value = drivers.get(group)
        if not isinstance(value, str):
            raise DriverConfigError(f"drivers.{group} must be a string.")
        value = value.strip().lower()
        if value not in allowed:
            choices = ", ".join(sorted(allowed))
            raise DriverConfigError(
                f"Invalid drivers.{group} value {value!r}; supported: {choices}."
            )
        normalized[group] = value

    return DriverConfiguration(
        schema_version=SCHEMA_VERSION,
        ch1_4=normalized["ch1_4"],
        ch5=normalized["ch5"],
        ch6=normalized["ch6"],
    )


def load_driver_config(path: str | Path | None = None) -> DriverConfiguration:
    """Load and strictly validate the driver configuration.

    A missing file uses the shipped Lowdriver-oriented defaults so an accidental
    deletion does not make the application unusable.  Invalid content is never
    guessed or silently corrected.
    """

    config_path = Path(path) if path is not None else DRIVER_CONFIG_PATH
    if not config_path.exists():
        return DriverConfiguration()
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DriverConfigError(
            f"Could not read driver configuration at {config_path}: {exc}"
        ) from exc
    return _validate(raw)


def get_driver_config() -> DriverConfiguration:
    """Return the process-wide configuration, loaded once until restart."""

    global _cached_config
    if _cached_config is None:
        _cached_config = load_driver_config()
    return _cached_config


def reset_driver_config_cache() -> None:
    """Test helper; production changes are intentionally restart-only."""

    global _cached_config
    _cached_config = None
