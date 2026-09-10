"""Persistence helpers for Workspace panel selection and default layout."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile

from backend.application_paths import USER_DATA_DIR
from .models import PanelType, WorkspaceState

WORKSPACE_SETTINGS_PATH = USER_DATA_DIR / "workspace_settings.json"
SCHEMA_VERSION = 1


def _panel(value):
    if value in (None, ""):
        return None
    try:
        return PanelType(str(value))
    except ValueError:
        return None


def load_workspace_settings(path: Path = WORKSPACE_SETTINGS_PATH):
    default = WorkspaceState()
    if not path.exists():
        return default, {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if int(payload.get("schema_version", 0)) != SCHEMA_VERSION:
            return default, {}
        custom = payload.get("custom") or {}
        state = WorkspaceState(
            left=_panel(custom.get("left")),
            right=_panel(custom.get("right")),
            split_ratio=float(custom.get("split_ratio", 0.5)),
        ).validated()
        ratios = {}
        for key, value in (payload.get("split_ratios") or {}).items():
            try:
                ratios[str(key)] = max(0.20, min(0.80, float(value)))
            except (TypeError, ValueError):
                continue
        return state, ratios
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return default, {}


def save_workspace_settings(
    state: WorkspaceState,
    split_ratios: dict[str, float],
    path: Path = WORKSPACE_SETTINGS_PATH,
) -> None:
    state = state.validated()
    path.parent.mkdir(parents=True, exist_ok=True)
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
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        handle.write(text)
        temp = Path(handle.name)
    temp.replace(path)


# Small compatibility helpers used by tests/older code.
def load_workspace_state(path: Path = WORKSPACE_SETTINGS_PATH) -> WorkspaceState:
    return load_workspace_settings(path)[0]


def save_workspace_state(state: WorkspaceState, path: Path = WORKSPACE_SETTINGS_PATH) -> None:
    save_workspace_settings(state, {}, path)
