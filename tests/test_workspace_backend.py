import json
import os
from pathlib import Path

import pytest

from backend.workspace.controller import WorkspaceController
from backend.workspace.models import PanelType, WorkspacePreset, WorkspaceState
from backend.workspace.persistence import load_workspace_settings, save_workspace_settings
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


# --- persistence regressions -------------------------------------------------

def _write_settings(path, payload):
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _corrupt_siblings(path):
    return sorted(p.name for p in path.parent.iterdir() if p.name.endswith(".corrupt"))


@pytest.mark.parametrize("raw", ["null", "[1, 2]", "123", '"custom"', "true"])
def test_non_dict_settings_payload_returns_defaults_instead_of_raising(tmp_path, raw):
    # ``payload.get(...)`` used to run before any type check, so a valid-JSON
    # wrong-type file raised AttributeError through WorkspaceController and
    # crashed app startup.
    path = tmp_path / "workspace_settings.json"
    path.write_text(raw, encoding="utf-8")
    state, ratios = load_workspace_settings(path)
    assert state == WorkspaceState()
    assert ratios == {}
    # Valid JSON with the wrong shape is unusable, so it is quarantined exactly
    # like the wrong-schema case below (and like backend/camera_profiles.py).
    assert _corrupt_siblings(path) == ["workspace_settings.json.corrupt"]


def test_wrong_schema_settings_are_quarantined_and_defaults_returned(tmp_path):
    path = _write_settings(
        tmp_path / "workspace_settings.json",
        {
            "schema_version": 999,
            "custom": {"left": "camera", "right": "sensors", "split_ratio": 0.5},
            "split_ratios": {"control": 0.34, "observation": 0.62},
        },
    )
    state, ratios = load_workspace_settings(path)
    assert state == WorkspaceState()
    assert ratios == {}
    # The rejected content must stay recoverable instead of being wiped.
    assert _corrupt_siblings(path) == ["workspace_settings.json.corrupt"]
    quarantined = json.loads(
        (tmp_path / "workspace_settings.json.corrupt").read_text(encoding="utf-8")
    )
    assert quarantined["split_ratios"] == {"control": 0.34, "observation": 0.62}


def test_duplicate_panel_settings_are_quarantined_not_silently_reset(tmp_path):
    # left == right makes validated() raise; that used to be swallowed by the
    # broad handler, wiping the layout and every per-preset ratio with no trace.
    path = _write_settings(
        tmp_path / "workspace_settings.json",
        {
            "schema_version": 1,
            "custom": {"left": "camera", "right": "camera", "split_ratio": 0.5},
            "split_ratios": {"control": 0.34, "custom": 0.71},
        },
    )
    state, ratios = load_workspace_settings(path)
    assert state == WorkspaceState()
    assert ratios == {}
    assert _corrupt_siblings(path) == ["workspace_settings.json.corrupt"]


def test_unparseable_settings_are_quarantined(tmp_path):
    path = tmp_path / "workspace_settings.json"
    path.write_text("{not json", encoding="utf-8")
    assert load_workspace_settings(path) == (WorkspaceState(), {})
    assert _corrupt_siblings(path) == ["workspace_settings.json.corrupt"]


def test_missing_settings_file_is_not_quarantined(tmp_path):
    path = tmp_path / "workspace_settings.json"
    assert load_workspace_settings(path) == (WorkspaceState(), {})
    assert list(tmp_path.iterdir()) == []


def test_saved_layout_and_ratios_round_trip(tmp_path):
    path = tmp_path / "workspace_settings.json"
    state = WorkspaceState(
        left=PanelType.CAMERA,
        right=PanelType.SENSORS,
        split_ratio=0.5,
    ).validated()
    save_workspace_settings(state, {"control": 0.34, "wave_test": 5.0}, path)
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored["schema_version"] == 1
    assert stored["custom"] == {"left": "camera", "right": "sensors", "split_ratio": 0.5}
    assert stored["split_ratios"] == {"control": 0.34, "wave_test": 0.80}
    assert load_workspace_settings(path) == (
        state,
        {"control": 0.34, "wave_test": 0.80},
    )


def test_save_flushes_content_before_the_rename(monkeypatch, tmp_path):
    # A rename that becomes durable before the content does can leave an
    # unusable settings file after a power cut.
    fsynced = []
    real_fsync = os.fsync
    monkeypatch.setattr(os, "fsync", lambda fd: (fsynced.append(fd), real_fsync(fd))[1])
    path = tmp_path / "workspace_settings.json"
    save_workspace_settings(WorkspaceState(), {}, path)
    assert fsynced, "the staged file must be fsync'd before it replaces the target"
    assert path.exists()


def test_failed_replace_leaves_no_temp_file_behind(monkeypatch, tmp_path):
    def boom(self, target):
        raise OSError("replace failed")

    monkeypatch.setattr(Path, "replace", boom)
    path = tmp_path / "workspace_settings.json"
    with pytest.raises(OSError):
        save_workspace_settings(WorkspaceState(), {}, path)
    assert sorted(p.name for p in tmp_path.iterdir()) == []
