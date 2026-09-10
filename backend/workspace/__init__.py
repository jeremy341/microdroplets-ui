"""Workspace layout backend.

This package owns only workspace composition and persistence.  Hardware remains
owned by the existing pump, sensor, wave and camera backends.
"""

from .models import PanelType, WorkspacePreset, WorkspaceState
from .controller import WorkspaceController
from .registry import PANEL_REGISTRY, WorkspacePanelDefinition, registered_panels

__all__ = [
    "PanelType", "WorkspacePreset", "WorkspaceState", "WorkspaceController",
    "PANEL_REGISTRY", "WorkspacePanelDefinition", "registered_panels",
]
