"""Shared automatic-wave execution service for one Multiboard.

Both the Wave editor and the Pumps page use this service.  It owns the runtime
WaveformRunner instances and routes their command stream through PumpControlService
so channel ownership and safe startup/shutdown are identical everywhere.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import threading
from typing import Callable

from backend.channel_ownership import ChannelOwnership
from backend.pump_control import PumpControlService
from backend.waveform_engine import (
    WaveStep,
    WaveformDefinition,
    WaveformRunner,
    driver_for_channel,
    generate_steps,
)


@dataclass(frozen=True)
class WaveRuntimeState:
    channel: int
    waveform_id: str
    waveform_name: str
    status: str
    current_step: int = 0
    total_steps: int = 0
    current_vpp: int | None = None
    message: str = ""


@dataclass
class _Execution:
    owner: ChannelOwnership
    definition: WaveformDefinition
    runner: WaveformRunner
    driver_index: int
    carrier_waveform: str
    first_amplitude_vpp: int
    started: bool = False


class WaveExecutionService:
    """Own all automatic waves for one connected board."""

    def __init__(self, pump_control: PumpControlService) -> None:
        self.pump_control = pump_control
        self._executions: dict[int, _Execution] = {}
        self._states: dict[int, WaveRuntimeState] = {}
        self._lock = threading.RLock()

    def start(
        self,
        definition: WaveformDefinition,
        channel: int,
        *,
        carrier_waveform: str = "Sinus",
        on_step: Callable[[int, WaveStep], None] | None = None,
        on_finished: Callable[[bool, str], None] | None = None,
    ) -> None:
        steps = tuple(generate_steps(definition, channel=channel))
        if not steps:
            raise ValueError("Waveform contains no generated steps.")

        owner = self.pump_control.claim_wave(channel, definition.name)
        driver_index = driver_for_channel(channel)

        def routed_send(command: str):
            return self._route_command(channel, command)

        def step_callback(index: int, step: WaveStep):
            with self._lock:
                state = self._states.get(channel)
                if state is not None:
                    self._states[channel] = replace(
                        state,
                        status="running",
                        current_step=index,
                        current_vpp=step.amplitude_vpp,
                    )
            if on_step is not None:
                on_step(index, step)

        def finished_callback(completed: bool, message: str):
            with self._lock:
                execution = self._executions.get(channel)
                owner_still_present = (
                    execution is not None
                    and self.pump_control.ownership.get(channel) is not None
                )
                final_status = "error" if owner_still_present else (
                    "completed" if completed else "stopped"
                )
                final_message = message
                if owner_still_present:
                    final_message = (
                        f"{message} · OFF was not confirmed; CH{channel} remains locked."
                    )
                state = self._states.get(channel)
                if state is not None:
                    self._states[channel] = replace(
                        state, status=final_status, message=final_message
                    )
            if on_finished is not None:
                on_finished(completed and not owner_still_present, final_message)

        runner = WaveformRunner(
            routed_send,
            on_step=step_callback,
            on_finished=finished_callback,
        )
        execution = _Execution(
            owner=owner,
            definition=definition.snapshot(),
            runner=runner,
            driver_index=driver_index,
            carrier_waveform=carrier_waveform,
            first_amplitude_vpp=steps[0].amplitude_vpp,
        )
        with self._lock:
            self._executions[channel] = execution
            self._states[channel] = WaveRuntimeState(
                channel=channel,
                waveform_id=definition.id,
                waveform_name=definition.name,
                status="starting",
                total_steps=len(steps),
            )
        try:
            runner.start(definition, channel, carrier_waveform=carrier_waveform)
        except Exception:
            with self._lock:
                self._executions.pop(channel, None)
                self._states.pop(channel, None)
            current = self.pump_control.ownership.get(channel)
            if current is not None and current.token == owner.token:
                self.pump_control.ownership.release(channel, owner.token)
            raise

    def _route_command(self, channel: int, command: str):
        with self._lock:
            execution = self._executions.get(channel)
        if execution is None:
            raise RuntimeError(f"No waveform execution is registered for CH{channel}.")

        clean = command.strip()
        on_command = f"P{channel}ON"
        off_command = f"P{channel}OFF"

        # OFF is handled even if startup failed before ``started`` became True.
        # That lets WaveformRunner's finally block retry the per-channel safe
        # stop when the startup rollback itself was not confirmed.
        if clean == off_command:
            if not self.pump_control.stop_wave(execution.owner):
                raise RuntimeError(f"CH{channel} OFF was not acknowledged")
            return True

        # WaveformRunner emits F/CS/V before ON. The shared pump service builds
        # those same values as one acknowledged startup transaction when ON is
        # reached, so the three preliminary writes are intentionally swallowed.
        if not execution.started:
            if clean != on_command:
                return True
            self.pump_control.start_wave(
                execution.owner,
                execution.driver_index,
                execution.carrier_waveform,
                execution.first_amplitude_vpp,
                frequency_hz=execution.definition.driver_frequency_hz,
            )
            execution.started = True
            with self._lock:
                state = self._states[channel]
                self._states[channel] = replace(state, status="running")
            return True

        prefix = f"P{channel}V"
        if clean.startswith(prefix):
            amplitude = int(clean[len(prefix):])
            self.pump_control.set_wave_amplitude(
                execution.owner, execution.driver_index, amplitude
            )
            return True

        raise RuntimeError(f"Unexpected waveform command for CH{channel}: {clean}")

    def stop(self, channel: int) -> bool:
        channel = int(channel)
        with self._lock:
            execution = self._executions.get(channel)
        if execution is None:
            return False
        if execution.runner.is_running:
            execution.runner.stop()
            return True

        # A finished runner can deliberately retain ownership when its OFF was
        # not acknowledged. Retry that stop in a worker so Qt never blocks.
        def retry_off():
            ok = False
            message = "OFF retry failed"
            try:
                ok = self.pump_control.stop_wave(execution.owner)
                message = "Stopped" if ok else f"CH{channel} OFF was not acknowledged"
            except Exception as exc:
                message = str(exc)
            with self._lock:
                state = self._states.get(channel)
                if state is not None:
                    self._states[channel] = replace(
                        state,
                        status="stopped" if ok else "error",
                        message=message,
                    )

        threading.Thread(
            target=retry_off, name=f"WaveStopRetry-CH{channel}", daemon=True
        ).start()
        return True

    def join(self, channel: int, timeout: float | None = None) -> None:
        with self._lock:
            execution = self._executions.get(int(channel))
        if execution is not None:
            execution.runner.join(timeout)

    def stop_all(self, timeout: float = 2.0) -> None:
        with self._lock:
            executions = list(self._executions.values())
        for execution in executions:
            execution.runner.stop()
        for execution in executions:
            execution.runner.join(timeout)
        # If a runner finished with an unconfirmed OFF, make one final
        # acknowledged per-channel retry before app.py falls back to POFF.
        for execution in executions:
            current = self.pump_control.ownership.get(execution.owner.channel)
            if current is not None and current.token == execution.owner.token:
                try:
                    self.pump_control.stop_wave(execution.owner)
                except Exception:
                    pass

    def force_release_after_disconnect(self) -> None:
        """Clear runtime state only after the physical connection is closed."""
        with self._lock:
            for execution in self._executions.values():
                execution.runner.stop()
            self._executions.clear()
            self._states.clear()
        self.pump_control.force_release_after_disconnect()

    def state(self, channel: int) -> WaveRuntimeState | None:
        with self._lock:
            return self._states.get(int(channel))

    def snapshot(self) -> dict[int, WaveRuntimeState]:
        with self._lock:
            return dict(self._states)

    def is_running(self, channel: int) -> bool:
        with self._lock:
            execution = self._executions.get(int(channel))
        return execution is not None and execution.runner.is_running

    def cleanup_finished(self) -> None:
        """Drop completed bookkeeping once ownership is definitely free."""
        with self._lock:
            removable = [
                channel for channel, execution in self._executions.items()
                if not execution.runner.is_running
                and self.pump_control.ownership.is_free(channel)
            ]
            for channel in removable:
                self._executions.pop(channel, None)
