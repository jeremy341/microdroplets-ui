"""Pure Workspace layout/preset controller with no hardware ownership.

Compact panels consume existing page/backend state; this controller stores only
which panels are shown and the default layout selection.
"""

from __future__ import annotations

from dataclasses import replace

from .models import PanelType, WorkspacePreset, WorkspaceState
from .persistence import load_workspace_settings, save_workspace_settings
from .presets import PRESET_STATES


class WorkspaceController:
    """Pure layout/state controller; it never performs a hardware action."""

    def __init__(self):
        self._custom_state, self._split_ratios = load_workspace_settings()
        self._state = self._custom_state

    @property
    def state(self) -> WorkspaceState:
        return self._state

    @property
    def custom_state(self) -> WorkspaceState:
        return self._custom_state

    def apply_preset(self, preset: WorkspacePreset) -> WorkspaceState:
        preset = WorkspacePreset(preset)
        if preset is WorkspacePreset.CUSTOM:
            self._state = self._custom_state
        else:
            base = PRESET_STATES[preset]
            ratio = self._split_ratios.get(preset.value, base.split_ratio)
            self._state = replace(base, split_ratio=ratio).validated()
        return self._state

    def set_panel(self, side: str, panel: PanelType | None) -> WorkspaceState:
        panel = PanelType(panel) if panel is not None else None
        left = self._state.left
        right = self._state.right
        if side == "left":
            left = panel
        elif side == "right":
            right = panel
        else:
            raise ValueError("side must be 'left' or 'right'")
        if left is not None and left == right:
            raise ValueError("The same Workspace panel cannot be selected twice.")
        self._custom_state = WorkspaceState(
            preset=WorkspacePreset.CUSTOM,
            left=left,
            right=right,
            split_ratio=self._state.split_ratio,
        ).validated()
        self._state = self._custom_state
        self._persist()
        return self._state

    def swap(self) -> WorkspaceState:
        self._custom_state = WorkspaceState(
            preset=WorkspacePreset.CUSTOM,
            left=self._state.right,
            right=self._state.left,
            split_ratio=1.0 - self._state.split_ratio,
        ).validated()
        self._state = self._custom_state
        self._persist()
        return self._state

    def clear(self) -> WorkspaceState:
        self._custom_state = WorkspaceState()
        self._state = self._custom_state
        self._persist()
        return self._state

    def set_split_ratio(self, ratio: float) -> WorkspaceState:
        updated = replace(self._state, split_ratio=float(ratio)).validated()
        self._state = updated
        self._split_ratios[updated.preset.value] = updated.split_ratio
        if updated.preset is WorkspacePreset.CUSTOM:
            self._custom_state = updated
        self._persist()
        return updated

    def save_current_as_custom(self) -> WorkspaceState:
        self._custom_state = WorkspaceState(
            preset=WorkspacePreset.CUSTOM,
            left=self._state.left,
            right=self._state.right,
            split_ratio=self._state.split_ratio,
        ).validated()
        self._split_ratios[WorkspacePreset.CUSTOM.value] = self._custom_state.split_ratio
        self._persist()
        return self._custom_state

    def _persist(self) -> None:
        save_workspace_settings(self._custom_state, self._split_ratios)
