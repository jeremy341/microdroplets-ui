from pathlib import Path


def _workspace_source():
    return (Path(__file__).parents[1] / "ui" / "pages" / "Workspace.py").read_text(encoding="utf-8")


def test_workspace_poll_does_not_overwrite_dragged_pump_sliders():
    source = _workspace_source()
    assert 'slot["frequency_slider"].isSliderDown()' in source
    assert 'slot["amplitude_slider"].isSliderDown()' in source
    assert 'workspaceUserEditing' in source
    assert 'if not frequency_editing:' in source
    assert 'if not amplitude_editing:' in source


def test_workspace_keeps_requested_values_visible_until_async_ack():
    source = _workspace_source()
    assert 'self._pending_frequency_by_driver' in source
    assert 'self._pending_waveform_by_driver' in source
    assert 'self._pending_amplitude_by_channel' in source
    assert 'self._pending_running_by_channel' in source
    assert 'self._pending_frequency_by_driver[driver_index] = value' in source
    assert 'self._pending_amplitude_by_channel[channel] = value' in source
    assert 'self._pending_running_by_channel[channel] = bool(desired)' in source


def test_shared_driver_pending_frequency_is_read_by_both_workspace_slots():
    source = _workspace_source()
    assert 'self._pending_frequency_by_driver.get(' in source
    assert 'board["pump_control"].get_driver_frequency(driver_index)' in source
