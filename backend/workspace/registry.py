"""Registry of panel types that may appear inside Workspace."""

from __future__ import annotations

from dataclasses import dataclass

from .models import PanelType


@dataclass(frozen=True)
class WorkspacePanelDefinition:
    panel_type: PanelType
    label: str
    description: str


PANEL_REGISTRY = {
    PanelType.PUMPS: WorkspacePanelDefinition(
        PanelType.PUMPS, "Pumps", "Control connected pump channels."
    ),
    PanelType.SENSORS: WorkspacePanelDefinition(
        PanelType.SENSORS, "Sensors", "View live sensor data and logging."
    ),
    PanelType.WAVE: WorkspacePanelDefinition(
        PanelType.WAVE, "Wave", "Generate and test pump waveforms."
    ),
    PanelType.CAMERA: WorkspacePanelDefinition(
        PanelType.CAMERA, "Camera", "Capture and view the live microscope."
    ),
}


def panel_definition(panel_type: PanelType) -> WorkspacePanelDefinition:
    return PANEL_REGISTRY[PanelType(panel_type)]


def registered_panels() -> tuple[WorkspacePanelDefinition, ...]:
    return tuple(PANEL_REGISTRY[panel] for panel in PanelType)
