from backend.channel_ownership import ChannelOwnershipManager, MANUAL, WAVEFORM
from backend.pump_control import PumpControlService
from backend.serial_manager import CommandSequenceResult


class FakeConnection:
    def __init__(self):
        self.is_open = True
        self.sequences = []
        self.raw = []
        self.next_success = True
        self.rollback_succeeded = True

    def send_sequence(self, commands, *, timeout=1.0, rollback_command=None):
        commands = tuple(commands)
        self.sequences.append((commands, rollback_command))
        if self.next_success:
            return CommandSequenceResult(True, commands)
        return CommandSequenceResult(
            False, commands, failed_command=commands[-1],
            rollback_command=rollback_command,
            rollback_succeeded=self.rollback_succeeded,
            message="not acknowledged",
        )

    def send(self, command):
        self.raw.append(command)


def test_manual_start_claims_only_after_safe_transaction_and_stop_releases():
    connection = FakeConnection()
    ownership = ChannelOwnershipManager()
    service = PumpControlService(connection, ownership)
    result = service.start_manual(0, 100, "Sinus", 2, 100)
    assert result.success
    assert ownership.get(2).kind == MANUAL
    assert connection.sequences[0][1] == "P2OFF"
    result = service.stop_manual(2)
    assert result.success
    assert ownership.is_free(2)


def test_failed_start_rolls_back_and_does_not_leave_false_manual_owner():
    connection = FakeConnection()
    connection.next_success = False
    connection.rollback_succeeded = True
    ownership = ChannelOwnershipManager()
    service = PumpControlService(connection, ownership)
    result = service.start_manual(0, 100, "Sinus", 1, 100)
    assert not result.success
    assert result.hardware_state == "off"
    assert ownership.is_free(1)


def test_wave_and_manual_paths_share_same_ownership_guard():
    connection = FakeConnection()
    ownership = ChannelOwnershipManager()
    service = PumpControlService(connection, ownership)
    wave = service.claim_wave(3, "Mixing Slow")
    assert wave.kind == WAVEFORM
    blocked = service.start_manual(0, 100, "Sinus", 3, 100)
    assert not blocked.success
    service.start_wave(wave, 0, "Sinus", 100)
    service.set_wave_amplitude(wave, 0, 110)
    assert connection.raw[-1] == "P3V110"
    assert service.stop_wave(wave)
    assert ownership.is_free(3)


def test_failed_start_with_unconfirmed_rollback_keeps_channel_locked():
    connection = FakeConnection()
    connection.next_success = False
    connection.rollback_succeeded = False
    ownership = ChannelOwnershipManager()
    service = PumpControlService(connection, ownership)
    result = service.start_manual(0, 100, "Sinus", 4, 100)
    assert not result.success
    assert result.hardware_state == "unknown"
    assert ownership.get(4) is not None
    assert ownership.get(4).kind == MANUAL
