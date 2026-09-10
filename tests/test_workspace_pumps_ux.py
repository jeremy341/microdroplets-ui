from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = (ROOT / "ui" / "pages" / "Workspace.py").read_text(encoding="utf-8")
STYLE = (ROOT / "style.qss").read_text(encoding="utf-8")


def test_workspace_mirrors_pumps_stopped_amplitude_visual_state():
    assert 'def _set_pump_stopped_visual(slot, stopped: bool):' in WORKSPACE
    assert 'self._set_pump_stopped_visual(slot, not running)' in WORKSPACE
    assert '("amplitude_slider", "amplitude_spin")' in WORKSPACE
    assert 'setProperty("pumpStopped", bool(stopped))' in WORKSPACE


def test_stopped_workspace_amplitude_remains_editable():
    # OFF is visual-only, just like the full Pumps page.  Actual enablement is
    # still controlled by pending operations / Wave ownership, not pump state.
    assert 'amplitude_editable = not operation_pending' in WORKSPACE
    assert 'and not running' not in WORKSPACE.split('amplitude_editable = not operation_pending', 1)[1].split('slot["amplitude_slider"].setEnabled', 1)[0]


def test_workspace_has_same_stopped_colours_as_pumps_page():
    assert '#workspacePumpPanel QSpinBox#workspaceSpin[pumpStopped="true"]' in STYLE
    assert 'background: #F1F4F2;' in STYLE
    assert 'color: #9BA5A0;' in STYLE
    assert '#BFCBC5' in STYLE


def test_frequency_does_not_get_channel_stopped_property():
    helper = WORKSPACE.split('def _set_pump_stopped_visual', 1)[1].split('@staticmethod', 1)[0]
    assert 'frequency_slider' not in helper
    assert 'frequency_spin' not in helper


def test_signal_mode_matches_driver_busy_semantics():
    assert 'waveform_editable = (' in WORKSPACE
    assert 'Stop all pumps on this driver before changing signal mode.' in WORKSPACE
