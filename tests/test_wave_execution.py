from dataclasses import replace
from threading import Event
from types import SimpleNamespace

import pytest

from backend.channel_ownership import (
    ChannelOwnership,
    ChannelOwnershipManager,
    MANUAL,
    WAVEFORM,
)
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


def long_wave(name="Long Wave", waveform_id="wave-long"):
    return WaveformDefinition(
        id=waveform_id, name=name, template="Square",
        min_vpp=80, max_vpp=100, increment_vpp=20,
        step_duration_ms=2000, cycle_duration_ms=10000, cycles=1,
    )


def test_orphaned_runner_cannot_route_a_command_through_a_newer_execution():
    # A runner whose thread outlives its registration (stop_all() gave up on
    # the join) must not act on the channel after a newer wave took it over:
    # its final OFF would stop the new wave and free the new owner.
    connection = FakeConnection()
    ownership = ChannelOwnershipManager()
    service = WaveExecutionService(PumpControlService(connection, ownership))

    stalled = Event()
    release = Event()
    orphan_finished = Event()
    reached_first_step = []

    def blocking_on_step(index, step):
        if not reached_first_step:
            reached_first_step.append(index)
            stalled.set()
            release.wait(2.0)

    def on_finished(completed, message):
        orphan_finished.set()

    service.start(wave(), 2, on_step=blocking_on_step, on_finished=on_finished)
    assert stalled.wait(1.0)

    # stop_all() cannot join the stalled worker, but it does release ownership,
    # so a new wave can claim CH2 while the orphan is still alive.
    service.stop_all(timeout=0.2)
    assert service.is_running(2)
    assert ownership.is_free(2)

    service.start(long_wave(), 2)
    owner_after_takeover = ownership.get(2)
    assert owner_after_takeover is not None

    release.set()
    assert orphan_finished.wait(2.0)
    service.join(2, 1.0)

    # The orphan's finally-OFF must not have released the new owner.
    assert ownership.get(2) is not None
    assert ownership.get(2).token == owner_after_takeover.token
    # Nor may the orphan overwrite the new wave's runtime state.
    state = service.state(2)
    assert state is not None
    assert state.status == "running"
    assert state.waveform_name == "Long Wave"
    service.stop(2)


def test_routing_refuses_a_command_from_a_superseded_execution():
    connection = FakeConnection()
    ownership = ChannelOwnershipManager()
    service = WaveExecutionService(PumpControlService(connection, ownership))
    service.start(long_wave(), 2)
    service.join(2, 1.0)
    sequences_before = list(connection.sequences)

    superseded = SimpleNamespace(
        owner=ChannelOwnership(2, WAVEFORM, "stale-token"),
        driver_index=0,
        carrier_waveform="Sinus",
        first_amplitude_vpp=80,
        definition=wave(),
        started=True,
    )
    with pytest.raises(RuntimeError):
        service._route_command(2, superseded, "P2OFF")
    with pytest.raises(RuntimeError):
        service._route_command(2, superseded, "P2V90")

    # Nothing reached the board and the real owner keeps its reservation.
    assert connection.sequences == sequences_before
    assert ownership.get(2) is not None
    service.stop(2)


def test_a_string_channel_runs_to_completion_and_is_accepted_by_the_identity_guard():
    # start() keys the registry with int(channel) while the ownership manager
    # normalized "2" to 2.  The mismatch used to make _is_current() reject the
    # legitimate runner's own commands, so the wave never ran at all.
    connection = FakeConnection()
    ownership = ChannelOwnershipManager()
    service = WaveExecutionService(PumpControlService(connection, ownership))
    seen_steps = []
    finished = []
    finished_event = Event()

    def on_step(index, step):
        seen_steps.append((index, step.amplitude_vpp))

    def on_finished(completed, message):
        finished.append((completed, message))
        finished_event.set()

    service.start(wave(), "2", on_step=on_step, on_finished=on_finished)
    assert finished_event.wait(2.0)
    service.join(2, 1.0)

    # The run completed instead of being refused as superseded/orphaned.
    assert finished == [(True, "Completed")]
    assert "no longer served" not in finished[0][1]

    # Its steps and commands reached the board, exactly like an int-keyed run.
    assert seen_steps
    assert connection.sequences[0][0][:4] == ("F0=100", "CS0=0", "P2V81", "P2ON")
    assert connection.sequences[0][1] == "P2OFF"
    assert connection.sequences[-1][0] == ("P2OFF",)
    assert "P2V97" in connection.raw
    assert ownership.is_free(2)

    state = service.state(2)
    assert state is not None
    assert state.status == "completed"


def test_state_stop_and_join_find_a_run_started_with_a_string_channel():
    connection = FakeConnection()
    ownership = ChannelOwnershipManager()
    service = WaveExecutionService(PumpControlService(connection, ownership))
    service.start(long_wave(), "2")

    # Every lookup by int finds the execution registered by the "2" start.
    assert service.is_running(2) is True
    state = service.state(2)
    assert state is not None
    assert state.channel == 2
    assert state.waveform_name == "Long Wave"
    assert list(service.snapshot()) == [2]

    service.join(2, 0.01)
    assert service.stop(2) is True
    service.join(2, 1.0)
    assert service.is_running(2) is False
    assert ownership.is_free(2)


def test_a_non_numeric_channel_is_rejected_with_a_clear_typed_error():
    connection = FakeConnection()
    ownership = ChannelOwnershipManager()
    service = WaveExecutionService(PumpControlService(connection, ownership))

    with pytest.raises(TypeError) as start_error:
        service.start(wave(), "two")
    assert "must be an integer" in str(start_error.value)

    # Nothing was keyed, claimed or sent on the way out.
    assert connection.sequences == []
    assert connection.raw == []
    assert ownership.all_free
    assert service.snapshot() == {}

    # Every channel-facing method shares the same normalization rule.
    with pytest.raises(TypeError):
        service.state("two")
    with pytest.raises(TypeError):
        service.stop("two")
