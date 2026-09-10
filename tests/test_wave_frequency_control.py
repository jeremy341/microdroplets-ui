from pathlib import Path

from backend.channel_ownership import ChannelOwnershipManager, MANUAL, WAVEFORM
from backend.pump_control import PumpControlService
from backend.serial_manager import CommandSequenceResult
from backend.wave_execution import WaveExecutionService
from backend.waveform_engine import WaveformDefinition
from backend.waveform_library import WaveformLibrary


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


def definition(*, frequency=175, minimum=80, maximum=100):
    return WaveformDefinition(
        id="v55-wave",
        name="Frequency test",
        template="Triangle",
        min_vpp=minimum,
        max_vpp=maximum,
        increment_vpp=20,
        step_duration_ms=20,
        cycle_duration_ms=100,
        cycles=1,
        driver_frequency_hz=frequency,
    )


def test_saved_wave_frequency_round_trips(tmp_path):
    path = tmp_path / "waveforms.json"
    library = WaveformLibrary(path)
    library.save(definition(frequency=175))
    reloaded = WaveformLibrary(path)
    assert reloaded.items[0].driver_frequency_hz == 175


def test_wave_execution_uses_saved_frequency_in_ack_start():
    connection = FakeConnection()
    service = WaveExecutionService(
        PumpControlService(connection, ChannelOwnershipManager())
    )
    service.start(definition(frequency=175), 2)
    service.join(2, 1.0)
    assert connection.sequences[0][0][0] == "F0=175"


def test_wave_can_start_on_highdriver4_while_sibling_manual_channel_is_owned():
    connection = FakeConnection()
    ownership = ChannelOwnershipManager()
    manual_owner = ownership.claim(1, MANUAL, label="existing pump")
    pump = PumpControlService(connection, ownership)
    wave_owner = pump.claim_wave(2, "wave test")

    assert pump.start_wave(
        wave_owner,
        0,
        "Sinus",
        81,
        frequency_hz=175,
    )

    assert connection.sequences[0][0][0] == "F0=175"
    assert ownership.get(1).token == manual_owner.token
    assert ownership.get(2).kind == WAVEFORM
    assert pump.get_driver_frequency(0) == 175


def test_different_driver_group_can_configure_frequency_while_ch1_is_owned():
    connection = FakeConnection()
    ownership = ChannelOwnershipManager()
    ownership.claim(1, MANUAL, label="existing pump")
    pump = PumpControlService(connection, ownership)
    wave_owner = pump.claim_wave(6, "independent test")

    assert pump.start_wave(
        wave_owner,
        2,
        "Sinus",
        100,
        frequency_hz=120,
    )
    assert connection.sequences[0][0][0] == "F2=120"


def test_live_frequency_change_during_wave_keeps_wave_ownership():
    connection = FakeConnection()
    ownership = ChannelOwnershipManager()
    pump = PumpControlService(connection, ownership)
    wave_owner = pump.claim_wave(2, "live frequency")
    assert pump.start_wave(wave_owner, 0, "Sinus", 81, frequency_hz=100)

    assert pump.set_driver_frequency(0, 150)
    assert connection.sequences[-1][0] == ("F0=150",)
    current = ownership.get(2)
    assert current is not None
    assert current.kind == WAVEFORM
    assert current.token == wave_owner.token
    assert pump.get_driver_frequency(0) == 150


def test_wave_page_no_longer_contains_mvp_read_only_or_active_frequency_lock():
    source = (Path(__file__).parents[1] / "ui" / "pages" / "Waveform.py").read_text(
        encoding="utf-8"
    )
    assert 'QLabel("Fixed for MVP")' not in source
    assert '"Driver frequency\\n(read-only)"' not in source
    assert 'self.frequency_edit.setReadOnly(True)' not in source
    assert "Frequency locked while this driver group is active" not in source
    assert "driver_group_busy" not in source
