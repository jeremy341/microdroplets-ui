"""Release contract for the Dino-Lite LED control exposed by the UI."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CAMERA = (ROOT / "ui/pages/Camera.py").read_text(encoding="utf-8")
WORKSPACE = (ROOT / "ui/pages/Workspace.py").read_text(encoding="utf-8")


def test_camera_exposes_led_on_off_without_unvalidated_intensity_control():
    assert 'self.led_switch = CameraSwitch(True)' in CAMERA
    assert 'self.led_switch.toggled.connect(self.apply_led_enabled)' in CAMERA
    assert 'CAMERA_LED_CONTROL' in CAMERA
    assert 'LED Intensity' not in CAMERA
    assert 'led_intensity_slider' not in CAMERA


def test_workspace_mirrors_led_on_off_only():
    assert 'self.led = CameraSwitch(False)' in WORKSPACE
    assert 'self.led.toggled.connect(self._led_changed)' in WORKSPACE
    assert 'page.led_switch' in WORKSPACE
    assert 'LED Intensity' not in WORKSPACE
    assert 'led_intensity' not in WORKSPACE


def test_camera_session_state_persists_led_switch_but_not_intensity():
    assert '"led_enabled": bool(self.led_switch.isChecked())' in CAMERA
    assert 'led_intensity_percent' not in CAMERA
