from pathlib import Path

from backend.workspace.controller import WorkspaceController
from backend.workspace.models import PanelType, WorkspacePreset, WorkspaceState
from backend.workspace.presets import PRESET_STATES
import backend.workspace.controller as controller_module


def test_workspace_presets_match_reference_combinations():
    assert PRESET_STATES[WorkspacePreset.CONTROL] == WorkspaceState(
        preset=WorkspacePreset.CONTROL,
        left=PanelType.PUMPS,
        right=PanelType.SENSORS,
        split_ratio=0.5,
    )
    assert PRESET_STATES[WorkspacePreset.WAVE_TEST].left is PanelType.WAVE
    assert PRESET_STATES[WorkspacePreset.WAVE_TEST].right is PanelType.SENSORS
    assert PRESET_STATES[WorkspacePreset.OBSERVATION].left is PanelType.CAMERA
    assert PRESET_STATES[WorkspacePreset.OBSERVATION].right is PanelType.SENSORS


def test_workspace_controller_rejects_duplicate_panels(monkeypatch, tmp_path):
    monkeypatch.setattr(controller_module, "load_workspace_settings", lambda: (WorkspaceState(), {}))
    monkeypatch.setattr(controller_module, "save_workspace_settings", lambda *args, **kwargs: None)
    controller = WorkspaceController()
    controller.set_panel("left", PanelType.CAMERA)
    try:
        controller.set_panel("right", PanelType.CAMERA)
    except ValueError:
        pass
    else:
        raise AssertionError("duplicate workspace panels must be rejected")


def test_workspace_layout_changes_are_pure_configuration(monkeypatch):
    writes = []
    monkeypatch.setattr(controller_module, "load_workspace_settings", lambda: (WorkspaceState(), {}))
    monkeypatch.setattr(controller_module, "save_workspace_settings", lambda *args, **kwargs: writes.append(args))
    controller = WorkspaceController()
    controller.apply_preset(WorkspacePreset.CONTROL)
    controller.swap()
    controller.clear()
    # Controller has no hardware/service dependency at all. Persistence writes
    # are the only side effect allowed for layout operations.
    assert writes
    assert not hasattr(controller, "pump_control")
    assert not hasattr(controller, "camera")
    assert not hasattr(controller, "wave_execution")


def test_custom_layout_survives_preset_switch(monkeypatch):
    monkeypatch.setattr(controller_module, "load_workspace_settings", lambda: (WorkspaceState(), {}))
    monkeypatch.setattr(controller_module, "save_workspace_settings", lambda *args, **kwargs: None)
    controller = WorkspaceController()
    controller.set_panel("left", PanelType.CAMERA)
    controller.set_panel("right", PanelType.PUMPS)
    custom = controller.custom_state
    controller.apply_preset(WorkspacePreset.OBSERVATION)
    assert controller.state.left is PanelType.CAMERA
    assert controller.state.right is PanelType.SENSORS
    controller.apply_preset(WorkspacePreset.CUSTOM)
    assert controller.state == custom


def test_split_ratio_is_fixed_to_half():
    assert WorkspaceState(split_ratio=0.01).validated().split_ratio == 0.5
    assert WorkspaceState(split_ratio=0.99).validated().split_ratio == 0.5
    assert WorkspaceState(split_ratio=0.5).validated().split_ratio == 0.5
