from dataclasses import replace

from backend.channel_ownership import ChannelOwnershipManager, MANUAL, WAVEFORM
from backend.pump_control import PumpControlService
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


def wave(channel=2):
    return WaveformDefinition(
        id="wave-1", name="Mixing Slow", template="Triangle",
        min_vpp=80, max_vpp=100, increment_vpp=20,
        step_duration_ms=20, cycle_duration_ms=100, cycles=1,
    )


def test_shared_wave_service_uses_ack_start_and_ack_off_but_timed_raw_steps():
    connection = FakeConnection()
    ownership = ChannelOwnershipManager()
    service = WaveExecutionService(PumpControlService(connection, ownership))
    service.start(wave(), 2)
    service.join(2, 1.0)
    assert connection.sequences[0][0][:4] == ("F0=100", "CS0=0", "P2V81", "P2ON")
    assert connection.sequences[0][1] == "P2OFF"
    assert connection.sequences[-1][0] == ("P2OFF",)
    assert "P2V97" in connection.raw
    assert ownership.is_free(2)


def test_manual_owner_blocks_wave_before_any_wave_command():
    connection = FakeConnection()
    ownership = ChannelOwnershipManager()
    pump = PumpControlService(connection, ownership)
    manual = ownership.claim(2, MANUAL, label="manual pump control")
    service = WaveExecutionService(pump)
    try:
        service.start(wave(), 2)
    except Exception as exc:
        assert "already in use" in str(exc)
    else:
        raise AssertionError("wave should be blocked")
    assert connection.sequences == []
    ownership.release(2, manual.token)


def test_different_channels_can_be_owned_independently():
    connection = FakeConnection()
    ownership = ChannelOwnershipManager()
    ownership.claim(1, MANUAL, label="manual pump control")
    service = WaveExecutionService(PumpControlService(connection, ownership))
    service.start(replace(wave(), id="wave-6", min_vpp=85, increment_vpp=5), 6)
    assert ownership.get(1).kind == MANUAL
    assert ownership.get(6).kind == WAVEFORM
    service.stop(6)
    service.join(6, 1.0)


def test_stop_all_surfaces_a_final_off_that_cannot_be_confirmed():
    # A final physical OFF that cannot be acknowledged must not be swallowed:
    # the channel state has to say the output state is unconfirmed.
    class UnconfirmedOffConnection(FakeConnection):
        def send_sequence(self, commands, *, timeout=1.0, rollback_command=None):
            commands = tuple(commands)
            self.sequences.append((commands, rollback_command))
            ok = any(not c.endswith("OFF") for c in commands)
            return CommandSequenceResult(ok, commands)

    connection = UnconfirmedOffConnection()
    ownership = ChannelOwnershipManager()
    service = WaveExecutionService(PumpControlService(connection, ownership))
    service.start(wave(), 2)
    service.join(2, 1.0)
    # The wave's own OFF was unconfirmed, so ownership is deliberately kept
    # and stop_all() performs the final acknowledged retry.
    service.stop_all(timeout=0.5)
    state = service.state(2)
    assert state is not None
    assert state.status == "error"
    assert "final OFF" in state.message
