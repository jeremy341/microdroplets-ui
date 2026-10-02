"""Persistence helpers for Workspace panel selection and default layout."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile

from backend.application_paths import USER_DATA_DIR
from .models import PanelType, WorkspaceState

WORKSPACE_SETTINGS_PATH = USER_DATA_DIR / "workspace_settings.json"
SCHEMA_VERSION = 1
CORRUPT_SUFFIX = ".corrupt"


class _RejectedSettings(ValueError):
    """Raised internally when a saved settings file cannot be used as-is."""


def _panel(value):
    if value in (None, ""):
        return None
    try:
        return PanelType(str(value))
    except ValueError:
        return None


def _quarantine(path: Path) -> None:
    """Move an unusable settings file aside before falling back to defaults.

    Falling back silently would destroy the user's layout and every per-preset
    ratio with no way to inspect or recover them. Keeping the rejected content
    as a ``.corrupt`` sibling (same convention as ``backend/camera_profiles.py``)
    makes the loss diagnosable and recoverable.
    """

    try:
        path.replace(path.with_name(path.name + CORRUPT_SUFFIX))
    except OSError:
        # Quarantining is best effort: never fail the load because of it.
        pass


def _split_ratios(payload: dict) -> dict:
    raw = payload.get("split_ratios")
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise _RejectedSettings("saved 'split_ratios' is not a JSON object")
    ratios = {}
    for key, value in raw.items():
        try:
            ratios[str(key)] = max(0.20, min(0.80, float(value)))
        except (TypeError, ValueError):
            continue
    return ratios


def _parse_settings(text: str):
    """Return ``(state, ratios)`` for a settings document.

    Raises ``ValueError`` (or a subclass) when the document cannot be used, so
    the caller can quarantine the file instead of silently resetting the user.
    """

    payload = json.loads(text)
    # Valid JSON with the wrong shape is as unusable as invalid JSON: ``null``,
    # a list or a bare number has no settings to restore.
    if not isinstance(payload, dict):
        raise _RejectedSettings("saved settings are not a JSON object")
    raw_version = payload.get("schema_version", 0)
    try:
        version = int(raw_version)
    except (TypeError, ValueError):
        version = None
    if version != SCHEMA_VERSION:
        raise _RejectedSettings(
            f"unsupported schema_version {raw_version!r} (expected {SCHEMA_VERSION})"
        )
    custom = payload.get("custom") or {}
    if not isinstance(custom, dict):
        raise _RejectedSettings("saved 'custom' layout is not a JSON object")
    state = WorkspaceState(
        left=_panel(custom.get("left")),
        right=_panel(custom.get("right")),
        split_ratio=float(custom.get("split_ratio", 0.5)),
    ).validated()
    return state, _split_ratios(payload)


def load_workspace_settings(path: Path = WORKSPACE_SETTINGS_PATH):
    default = WorkspaceState()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        # A missing settings file is the normal first-run case, not corruption.
        return default, {}
    try:
        return _parse_settings(text)
    except (OSError, ValueError, TypeError, AttributeError, json.JSONDecodeError):
        # Covers unparseable JSON, a schema mismatch and a state rejected by
        # ``validated()`` (e.g. left == right). Quarantine first so the rejected
        # content stays recoverable, then fall back to the defaults.
        _quarantine(path)
        return default, {}


def _fsync_directory(directory: Path) -> None:
    """Best-effort durability for the rename itself (POSIX only)."""

    try:
        handle = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(handle)
    except OSError:
        pass
    finally:
        os.close(handle)


def _write_durable(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` atomically, without leaking a temp file."""

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            # The content has to reach the disk before the rename becomes
            # visible, otherwise a power cut can leave a truncated or empty
            # settings file that the next load would have to quarantine.
            handle.flush()
            os.fsync(handle.fileno())
        temporary_path.replace(path)
        _fsync_directory(path.parent)
    finally:
        # A failed replace must not leave a temp file behind in user data.
        temporary_path.unlink(missing_ok=True)


def save_workspace_settings(
    state: WorkspaceState,
    split_ratios: dict[str, float],
    path: Path = WORKSPACE_SETTINGS_PATH,
) -> None:
    state = state.validated()
    payload = {
        "schema_version": SCHEMA_VERSION,
        "custom": {
            "left": state.left.value if state.left else None,
            "right": state.right.value if state.right else None,
            "split_ratio": state.split_ratio,
        },
        "split_ratios": {
            str(key): max(0.20, min(0.80, float(value)))
            for key, value in split_ratios.items()
        },
    }
    text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    _write_durable(path, text)


# Small compatibility helpers used by tests/older code.
def load_workspace_state(path: Path = WORKSPACE_SETTINGS_PATH) -> WorkspaceState:
    return load_workspace_settings(path)[0]


def save_workspace_state(state: WorkspaceState, path: Path = WORKSPACE_SETTINGS_PATH) -> None:
    save_workspace_settings(state, {}, path)