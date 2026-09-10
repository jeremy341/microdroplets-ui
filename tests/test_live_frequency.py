from pathlib import Path
from time import sleep

import pytest

from backend.channel_ownership import ChannelOwnershipManager, MANUAL, WAVEFORM
from backend.pump_control import PumpControlService
from backend.protocol import driver_amplitude_command, driver_frequency_command, pump_start_commands
from backend.serial_manager import CommandSequenceResult
from backend.wave_execution import WaveExecutionService
from backend.waveform_engine import WaveformDefinition


class FakeConnection:
    def __init__(self):
        self.is_open = True
        self.sequences = []
        self.raw = []

    def send_sequence(self, commands, *, timeout=1.0, rollback_command=None):
        commands = tuple(commands)
        self.sequences.append((commands, rollback_command))
        return CommandSequenceResult(True, commands)

    def send(self, command):
        self.raw.append(command)


def test_default_lowdriver_ch5_start_sequence_uses_f1_without_cs1():
    commands = pump_start_commands(1, 175, "Sinus", 5, 25)
    assert commands == ("F1=175", "P5V25", "P5ON")
    assert not any(command.startswith("CS1=") for command in commands)


def test_default_lowdriver_ch5_protocol_limits():
    assert driver_amplitude_command(1, 5, 0) == "P5V0"
    assert driver_amplitude_command(1, 5, 150) == "P5V150"
    assert driver_frequency_command(1, 8) == "F1=8"
    assert driver_frequency_command(1, 800) == "F1=800"
    with pytest.raises(ValueError):
        driver_amplitude_command(1, 5, 151)
    with pytest.raises(ValueError):
        driver_frequency_command(1, 7)


def test_live_f1_change_while_ch5_manual_does_not_change_other_domains():
    connection = FakeConnection()
    ownership = ChannelOwnershipManager()
    ownership.claim(5, MANUAL, label="manual")
    service = PumpControlService(connection, ownership)
    before = service.frequency_state.snapshot()

    assert service.set_driver_frequency(1, 187)

    after = service.frequency_state.snapshot()
    assert after[1] == 187
    assert after[0] == before[0]
    assert after[2] == before[2]
    assert ownership.get(5).kind == MANUAL


def test_live_f1_change_while_ch5_wave_keeps_wave_owner():
    connection = FakeConnection()
    ownership = ChannelOwnershipManager()
    owner = ownership.claim(5, WAVEFORM, label="wave")
    service = PumpControlService(connection, ownership)

    assert service.set_driver_frequency(1, 200)
    current = ownership.get(5)
    assert current is not None and current.token == owner.token
    assert service.get_driver_frequency(1) == 200




def test_live_frequency_write_does_not_restart_running_wave_timeline():
    connection = FakeConnection()
    pump = PumpControlService(connection, ChannelOwnershipManager())
    waves = WaveExecutionService(pump)
    wave = WaveformDefinition(
        id="v63-live",
        name="Live frequency",
        template="Triangle",
        min_vpp=20,
        max_vpp=40,
        increment_vpp=10,
        step_duration_ms=20,
        cycle_duration_ms=200,
        cycles=2,
        driver_frequency_hz=175,
    )

    waves.start(wave, 5)
    sleep(0.06)
    state_before = waves.state(5)
    assert state_before is not None and state_before.status == "running"
    step_before = state_before.current_step

    assert pump.set_driver_frequency(1, 200)
    sleep(0.06)
    state_after = waves.state(5)
    assert state_after is not None and state_after.status == "running"
    assert state_after.current_step > step_before
    assert pump.get_driver_frequency(1) == 200

    waves.join(5, 2.0)
    final = waves.state(5)
    assert final is not None and final.status == "completed"
    # Startup occurs once; the live F1 update is a separate transaction and
    # never sends a second P5ON/restart sequence.
    assert sum("P5ON" in commands for commands, _ in connection.sequences) == 1
    assert any(commands == ("F1=200",) for commands, _ in connection.sequences)

def test_frequency_state_can_be_seeded_from_safe_session_ram_without_serial_write():
    connection = FakeConnection()
    service = PumpControlService(connection, ChannelOwnershipManager())
    service.seed_driver_frequencies({0: 125, 1: 175, 2: 120})
    assert service.frequency_state.snapshot() == {0: 125, 1: 175, 2: 120}
    assert connection.sequences == []


def test_no_frequency_idle_lock_text_remains_in_pump_or_wave_pages():
    root = Path(__file__).parents[1]
    pumps = (root / "ui" / "pages" / "Pumps.py").read_text(encoding="utf-8")
    wave = (root / "ui" / "pages" / "Waveform.py").read_text(encoding="utf-8")
    assert "Stop all pumps on this driver before changing frequency" not in pumps
    assert "Frequency locked while this driver group is active" not in wave


def test_global_topbar_contains_configured_ch5_driver_flag_without_qss_change():
    root = Path(__file__).parents[1]
    app_source = (root / "app.py").read_text(encoding="utf-8")
    assert 'QLabel(f"CH5 · {ch5_driver_capabilities.display_name}")' in app_source
    assert "DRIVER_CONFIG_PATH" in app_source
