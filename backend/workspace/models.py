"""Small immutable models shared by Workspace UI and persistence."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum


class PanelType(str, Enum):
    PUMPS = "pumps"
    SENSORS = "sensors"
    WAVE = "wave"
    CAMERA = "camera"


class WorkspacePreset(str, Enum):
    CUSTOM = "custom"
    CONTROL = "control"
    WAVE_TEST = "wave_test"
    OBSERVATION = "observation"


@dataclass(frozen=True)
class WorkspaceState:
    preset: WorkspacePreset = WorkspacePreset.CUSTOM
    left: PanelType | None = None
    right: PanelType | None = None
    split_ratio: float = 0.5

    def validated(self) -> "WorkspaceState":
        if self.left is not None and self.left == self.right:
            raise ValueError("The same Workspace panel cannot be selected twice.")
        # Every freshly rendered Workspace starts at 50/50. The live QSplitter
        # remains draggable, but a reopened/preset Workspace intentionally
        # returns to the validated 0.5 default instead of persisting an old drag.
        return replace(self, split_ratio=0.5)

    @property
    def complete(self) -> bool:
        return self.left is not None and self.right is not None
