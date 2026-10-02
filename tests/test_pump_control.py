import pytest

from backend.channel_ownership import ChannelOwnershipManager, ChannelOwnershipError, MANUAL, WAVEFORM
from backend.pump_control import PumpControlService, PumpOperationResult
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


def test_start_manual_on_wave_owned_channel_returns_failure_not_exception():
    """Contention is a documented outcome of the command, never an exception.

    Callers hold the ``PumpOperationResult`` contract and must never have to
    catch ``ChannelOwnershipError`` out of ``start_manual``.
    """

    connection = FakeConnection()
    ownership = ChannelOwnershipManager()
    service = PumpControlService(connection, ownership)
    ownership.claim(2, WAVEFORM, label="Mixing Slow")

    result = service.start_manual(0, 100, "Sinus", 2, 100)

    assert isinstance(result, PumpOperationResult)
    assert result.success is False
    assert result.channel == 2
    assert result.desired_enabled is True
    assert result.ownership is None
    # Nothing was sent and nothing was reserved.
    assert result.hardware_state == "unchanged"
    assert connection.sequences == []
    assert ownership.get(2).label == "Mixing Slow"
    assert ownership.get(2).kind == WAVEFORM


def test_start_manual_contention_message_names_channel_and_conflicting_owner():
    connection = FakeConnection()
    ownership = ChannelOwnershipManager()
    service = PumpControlService(connection, ownership)
    ownership.claim(3, WAVEFORM, label="Mixing Fast")

    result = service.start_manual(0, 100, "Sinus", 3, 100)

    assert not result.success
    assert "CH3" in result.message
    assert "Mixing Fast" in result.message


def test_start_manual_contention_on_unlabelled_owner_names_the_owner_kind():
    connection = FakeConnection()
    ownership = ChannelOwnershipManager()
    service = PumpControlService(connection, ownership)
    ownership.claim(4, WAVEFORM)

    result = service.start_manual(0, 100, "Sinus", 4, 100)

    assert not result.success
    assert "CH4" in result.message
    assert WAVEFORM in result.message


def test_force_release_paths_return_status_and_never_raise_on_contention():
    """``PumpControlService`` exposes no ``force_release``; pin what does exist.

    The release surface is ``force_release_after_disconnect`` plus the manager's
    ``force_release``. Both are cleanup after a confirmed disconnect, so they
    must report status instead of raising ``ChannelOwnershipError`` at the
    contended channel.
    """

    connection = FakeConnection()
    ownership = ChannelOwnershipManager()
    service = PumpControlService(connection, ownership)
    assert not hasattr(service, "force_release")
    ownership.claim(1, MANUAL, label="manual pump control")
    ownership.claim(5, WAVEFORM, label="Mixing Slow")

    assert ownership.force_release(5) is True
    assert ownership.is_free(5)
    assert ownership.force_release(5) is False
    assert ownership.get(1).kind == MANUAL

    service.force_release_after_disconnect()
    assert ownership.all_free


def test_start_manual_rejects_invalid_arguments_without_reserving_the_channel():
    """Validation runs before the claim, so a rejected call leaks no channel."""

    connection = FakeConnection()
    ownership = ChannelOwnershipManager()
    service = PumpControlService(connection, ownership)
    # Driver 0 owns CH1-CH4 at 50-800 Hz and 10-250 Vpp.
    invalid_calls = [
        (0, 9999, "Sinus", 1, 100),      # frequency above the driver limit
        (0, 10, "Sinus", 2, 100),        # frequency below the driver limit
        (0, 100, "Sinus", 3, 5),         # amplitude below the driver limit
        (0, 100, "Sinus", 4, 999),       # amplitude above the driver limit
        (0, 100, "Not A Waveform", 1, 100),  # unsupported carrier shape
        (0, 100, "Sinus", 5, 100),       # CH5 does not belong to driver 0
    ]

    for driver_index, frequency_hz, waveform, channel, amplitude in invalid_calls:
        result = service.start_manual(driver_index, frequency_hz, waveform, channel, amplitude)

        assert not result.success, (channel, result)
        assert result.channel == channel
        # Nothing was transmitted, so the output is exactly as it was.
        assert result.hardware_state == "unchanged"
        assert result.ownership is None
        assert result.message
        # The rejected call must leave the channel free for the next caller.
        assert ownership.is_free(channel), channel

    assert connection.sequences == []


def test_channel_left_free_by_rejected_start_can_be_claimed_successfully():
    connection = FakeConnection()
    ownership = ChannelOwnershipManager()
    service = PumpControlService(connection, ownership)

    rejected = service.start_manual(0, 9999, "Sinus", 2, 100)
    assert not rejected.success
    assert ownership.is_free(2)

    accepted = service.start_manual(0, 100, "Sinus", 2, 100)
    assert accepted.success
    assert accepted.hardware_state == "on"
    assert ownership.get(2).kind == MANUAL

    assert service.stop_manual(2).success
    assert ownership.is_free(2)


def test_start_manual_still_lets_the_ownership_error_type_reach_claim_callers():
    """The service catches the error narrowly; the type itself is unchanged."""

    ownership = ChannelOwnershipManager()
    ownership.claim(6, WAVEFORM, label="Mixing Slow")

    with pytest.raises(ChannelOwnershipError, match="Mixing Slow"):
        ownership.claim(6, MANUAL, label="manual pump control")

    assert issubclass(ChannelOwnershipError, RuntimeError)
