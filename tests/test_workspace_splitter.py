from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = (ROOT / "ui/pages/Workspace.py").read_text(encoding="utf-8")
PRESETS = (ROOT / "backend/workspace/presets.py").read_text(encoding="utf-8")


def test_workspace_splitter_is_user_adjustable():
    assert "handle.setEnabled(True)" in WORKSPACE
    assert "Qt.CursorShape.SplitHCursor" in WORKSPACE
    assert "handle.setEnabled(False)" not in WORKSPACE


def test_workspace_starts_at_half_split_only_once():
    assert "self._apply_split_ratio(s, 0.5)" in WORKSPACE
    assert "splitter.width() - splitter.handleWidth()" in WORKSPACE
    assert "splitter.setSizes([left, width - left])" in WORKSPACE
    assert "resizeEvent" not in WORKSPACE.split("def _apply_split_ratio", 1)[1].split("def _destroy_active_panels", 1)[0]


def test_all_presets_still_default_to_half_split():
    assert PRESETS.count("split_ratio=0.5") == 3
    assert "split_ratio=0.34" not in PRESETS
    assert "split_ratio=0.42" not in PRESETS
    assert "split_ratio=0.67" not in PRESETS


def test_camera_workspace_can_shrink_to_the_half_width_target():
    camera = WORKSPACE.split("class CameraWorkspacePanel", 1)[1].split("class WorkspacePage", 1)[0]
    assert "self.setMinimumWidth(0)" in camera
    assert "QSizePolicy.Policy.Ignored" in camera
