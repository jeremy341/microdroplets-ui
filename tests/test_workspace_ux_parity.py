from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = (ROOT / "ui" / "pages" / "Workspace.py").read_text(encoding="utf-8")
STYLE = (ROOT / "style.qss").read_text(encoding="utf-8")


def test_wave_workspace_uses_real_connection_state():
    assert 'connection = page.active_board.get("connection") if page.active_board else None' in WORKSPACE
    assert 'board_ready = bool(connection is not None and getattr(connection, "is_open", True))' in WORKSPACE


def test_wave_workspace_locks_editor_but_keeps_frequency_live():
    for name in (
        'self.channel_combo', 'self.template_combo',
        'self.min_spin', 'self.max_spin', 'self.increment_spin',
        'self.duration_spin', 'self.cycles_spin',
    ):
        assert name in WORKSPACE
    assert 'control.setEnabled(not running)' in WORKSPACE
    assert 'self.saved_combo.setEnabled((not running) and bool(page.library.items))' in WORKSPACE
    assert 'self.frequency_spin.setEnabled(True)' in WORKSPACE
    assert 'can_test = editor_valid and compatibility.valid and board_ready and not running and not manual_busy' in WORKSPACE


def test_wave_workspace_explains_manual_pump_and_styles_test_button():
    assert 'Turn the pump off before starting a waveform.' in WORKSPACE
    assert 'self.test_button.setText("Pump in use" if manual_busy else "Test Waveform")' in WORKSPACE
    assert 'self.test_button.setProperty("blockedByPump", manual_busy)' in WORKSPACE
    assert '#workspaceWavePanel #workspacePrimaryAction[blockedByPump="true"]:disabled' in STYLE


def test_pump_workspace_matches_full_page_backend_only_wave_ownership_ux():
    assert 'amplitude_editable = not operation_pending' in WORKSPACE
    assert 'slot["toggle"].setEnabled(not operation_pending)' in WORKSPACE
    assert 'owner is not None and owner.kind == "waveform"' in WORKSPACE
