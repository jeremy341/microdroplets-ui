"""Static regression coverage for Workspace <-> full-page state synchronization."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = (ROOT / "ui/pages/Workspace.py").read_text(encoding="utf-8")
PUMPS = (ROOT / "ui/pages/Pumps.py").read_text(encoding="utf-8")
SENSORS = (ROOT / "ui/pages/Sensors.py").read_text(encoding="utf-8")


def test_pumps_workspace_and_full_page_share_runtime_values_both_ways():
    assert "PumpWorkspacePanel(self.active_board_provider, self.pumps_page)" in WORKSPACE
    assert "def refresh_from_shared_state(self):" in PUMPS
    assert "def _sync_driver_control_values(self, driver_data):" in PUMPS
    assert "def _sync_channel_control_values(self, channel_data):" in PUMPS
    assert "self._refresh_full_pumps_page()" in WORKSPACE
    # The full page must not fight a drag/edit while polling shared state.
    assert "not slider.isSliderDown() and not spinbox.hasFocus()" in PUMPS


def test_sensor_selection_and_view_mode_are_shared_with_full_tab():
    assert "def set_primary_measurement(self, board_id, sensor_id):" in SENSORS
    assert "self.sensors_page.set_primary_measurement(board_id, sensor_id)" in WORKSPACE
    assert "self.sensors_page.selected_measurements.get(board_id)" in WORKSPACE
    assert "self.sensors_page.chart_pause()" in WORKSPACE
    assert "self.sensors_page.chart_fit_data()" in WORKSPACE
    assert "self.sensors_page.chart_auto_scale()" in WORKSPACE
    assert "self._sync_chart_mode_from_page()" in WORKSPACE


def test_wave_workspace_edits_the_existing_wave_page_draft():
    assert "page.channel_combo.setCurrentIndex" in WORKSPACE
    assert "page.template_combo.setCurrentText" in WORKSPACE
    assert "page.min_spin.setValue" in WORKSPACE
    assert "page.frequency_edit.setValue" in WORKSPACE
    assert "page._editor_changed()" in WORKSPACE
    # Reverse direction: Workspace refresh reads the same full-page widgets.
    assert "self.template_combo.setCurrentText(page.template_combo.currentText())" in WORKSPACE
    assert "self.frequency_spin.setValue(page.frequency_edit.value())" in WORKSPACE


def test_camera_workspace_edits_and_reads_existing_camera_page_controls():
    assert "self.camera_page.resolution_combo.setCurrentIndex" in WORKSPACE
    assert "self.camera_page.fps_combo.setCurrentIndex" in WORKSPACE
    assert "self.camera_page.auto_exposure.setChecked" in WORKSPACE
    assert "self.camera_page.brightness_slider.setValue" in WORKSPACE
    assert "page.camera.latest_frame()" in WORKSPACE
    assert "self._sync_combo(self.resolution, page.resolution_combo)" in WORKSPACE
