"""Built-in two-panel Workspace presets."""

from __future__ import annotations

from .models import PanelType, WorkspacePreset, WorkspaceState


PRESET_STATES = {
    WorkspacePreset.CONTROL: WorkspaceState(
        preset=WorkspacePreset.CONTROL,
        left=PanelType.PUMPS,
        right=PanelType.SENSORS,
        split_ratio=0.5,
    ),
    WorkspacePreset.WAVE_TEST: WorkspaceState(
        preset=WorkspacePreset.WAVE_TEST,
        left=PanelType.WAVE,
        right=PanelType.SENSORS,
        split_ratio=0.5,
    ),
    WorkspacePreset.OBSERVATION: WorkspaceState(
        preset=WorkspacePreset.OBSERVATION,
        left=PanelType.CAMERA,
        right=PanelType.SENSORS,
        split_ratio=0.5,
    ),
}

PRESET_TITLES = {
    WorkspacePreset.CUSTOM: "Workspace",
    WorkspacePreset.CONTROL: "Control Workspace",
    WorkspacePreset.WAVE_TEST: "Wave Test Workspace",
    WorkspacePreset.OBSERVATION: "Observation Workspace",
}

PRESET_LABELS = {
    WorkspacePreset.CUSTOM: "Custom",
    WorkspacePreset.CONTROL: "Control — Pumps + Sensors",
    WorkspacePreset.WAVE_TEST: "Wave Test — Wave + Sensors",
    WorkspacePreset.OBSERVATION: "Observation — Camera + Sensors",
}
