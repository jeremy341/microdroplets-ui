"""Shared safe pump-control backend used by both Pumps and Wave pages."""

from __future__ import annotations

from dataclasses import dataclass
import threading

from backend.driver_capabilities import capabilities_for_driver_index
from backend.driver_frequency_state import DriverFrequencyState

from backend.channel_ownership import (
    ChannelOwnership,
    ChannelOwnershipError,
    ChannelOwnershipManager,
    MANUAL,
    WAVEFORM,
)
from backend.protocol import (
    PUMP_DRIVER_CHANNELS,
    driver_amplitude_command,
    driver_frequency_command,
    driver_waveform_command,
    pump_start_commands,
    pump_state_command,
)


@dataclass(frozen=True)
class PumpOperationResult:
    success: bool
    channel: int | None = None
    desired_enabled: bool | None = None
    message: str = ""
    ownership: ChannelOwnership | None = None
    hardware_state: str = "known"


class PumpControlService:
    """Serialize pump actions and enforce ownership before touching hardware."""

    def __init__(
        self,
        connection,
        ownership: ChannelOwnershipManager,
        *,
        ack_timeout=1.0,
        frequency_state: DriverFrequencyState | None = None,
    ):
        self.connection = connection
        self.ownership = ownership
        self.ack_timeout = float(ack_timeout)
        self.frequency_state = frequency_state or DriverFrequencyState()
        self._operation_lock = threading.RLock()

    @staticmethod
    def _same_owner(current: ChannelOwnership | None, owner: ChannelOwnership) -> bool:
        return current is not None and current.token == owner.token and current.kind == owner.kind

    def _require_owner(self, owner: ChannelOwnership) -> None:
        current = self.ownership.get(owner.channel)
        if not self._same_owner(current, owner):
            raise ChannelOwnershipError(
                f"CH{owner.channel} is no longer owned by {owner.label or owner.kind}."
            )

    def start_manual(self, driver_index: int, frequency_hz: int, carrier_waveform: str,
                     channel: int, amplitude_vpp: int) -> PumpOperationResult:
        with self._operation_lock:
            try:
                owner = self.ownership.claim(channel, MANUAL, label="manual pump control")
            except ChannelOwnershipError as exc:
                return PumpOperationResult(False, channel, True, str(exc), hardware_state="unchanged")

            rollback_command = pump_state_command(channel, False)
            try:
                result = self.connection.send_sequence(
                    pump_start_commands(
                        driver_index,
                        frequency_hz,
                        carrier_waveform,
                        channel,
                        amplitude_vpp,
                    ),
                    timeout=self.ack_timeout,
                    rollback_command=rollback_command,
                )
            except Exception as exc:
                # A transport exception does not prove the physical output is
                # OFF. Retain ownership so no second control path can take over
                # until the user performs a confirmed stop/disconnect.
                return PumpOperationResult(
                    False, channel, True, str(exc), owner, hardware_state="unknown"
                )

            if not result.success:
                state = "off" if result.rollback_succeeded else "unknown"
                if result.rollback_succeeded:
                    self.ownership.release(channel, owner.token)
                    retained_owner = None
                else:
                    retained_owner = owner
                return PumpOperationResult(
                    False, channel, True,
                    result.message or "Pump start was not acknowledged.",
                    retained_owner,
                    hardware_state=state,
                )
            self.frequency_state.set(driver_index, frequency_hz)
            return PumpOperationResult(True, channel, True, "Pump started", owner, "on")

    def stop_manual(self, channel: int) -> PumpOperationResult:
        with self._operation_lock:
            owner = self.ownership.get(channel)
            if owner is None or owner.kind != MANUAL:
                return PumpOperationResult(False, channel, False, f"CH{channel} is not under manual control.", hardware_state="unchanged")
            result = self.connection.send_sequence(
                (pump_state_command(channel, False),), timeout=self.ack_timeout
            )
            if not result.success:
                # Keep ownership when OFF was not confirmed. This intentionally
                # blocks a waveform from taking over an output with unknown state.
                return PumpOperationResult(False, channel, False, result.message, owner, "unknown")
            self.ownership.release(channel, owner.token)
            return PumpOperationResult(True, channel, False, "Pump stopped", None, "off")

    def set_manual_amplitude(self, driver_index: int, channel: int, amplitude_vpp: int) -> PumpOperationResult:
        with self._operation_lock:
            owner = self.ownership.get(channel)
            if owner is None or owner.kind != MANUAL:
                return PumpOperationResult(False, channel, None, f"CH{channel} is not under manual control.", hardware_state="unchanged")
            result = self.connection.send_sequence(
                (driver_amplitude_command(driver_index, channel, amplitude_vpp),),
                timeout=self.ack_timeout,
            )
            return PumpOperationResult(
                result.success, channel, None,
                result.message or ("Amplitude updated" if result.success else "Amplitude update failed"),
                owner, "on" if result.success else "unknown",
            )

    def _driver_group_is_idle(self, driver_index: int) -> bool:
        """Return True when no channel on this physical driver is owned.

        This guard is intentionally used only for the shared carrier-shape
        setting (``CS<d>``). Driver frequency is a live property and is
        allowed to change while manual Pumps or Waves are active.
        """

        channels = PUMP_DRIVER_CHANNELS.get(int(driver_index))
        if channels is None:
            raise ValueError("Driver index must be 0, 1, or 2")
        return all(self.ownership.is_free(channel) for channel in channels)

    def get_driver_frequency(self, driver_index: int) -> int:
        """Return the last acknowledged frequency for one physical driver domain."""

        return self.frequency_state.get(driver_index)

    def seed_driver_frequencies(self, values: dict[int, int]) -> None:
        """Synchronize RAM/session configuration without sending hardware commands."""

        self.frequency_state.replace(values)

    def set_driver_frequency(self, driver_index: int, value: int) -> bool:
        """Apply a live driver frequency, even while Pumps/Waves are active.

        Frequency is a physical driver property, not an exclusive channel-owned
        value.  F0 therefore affects CH1-CH4 together, F1 affects CH5, and F2
        affects CH6.  The operation lock serializes this acknowledged write with
        amplitude/start/stop traffic without requiring the driver group to stop.
        """

        with self._operation_lock:
            result = self.connection.send_sequence(
                (driver_frequency_command(driver_index, value),), timeout=self.ack_timeout
            )
            if result.success:
                self.frequency_state.set(driver_index, value)
            return result.success

    def set_driver_waveform(self, driver_index: int, waveform: str) -> bool:
        """Change shared carrier shape only while its complete driver is idle."""

        with self._operation_lock:
            if not self._driver_group_is_idle(driver_index):
                return False
            if not capabilities_for_driver_index(driver_index).supports_carrier_waveform:
                return False
            result = self.connection.send_sequence(
                (driver_waveform_command(driver_index, waveform),), timeout=self.ack_timeout
            )
            return result.success

    # ----------------------------- automatic wave ownership/hardware boundary
    def claim_wave(self, channel: int, label: str) -> ChannelOwnership:
        return self.ownership.claim(channel, WAVEFORM, label=label)

    def start_wave(self, owner: ChannelOwnership, driver_index: int, carrier_waveform: str,
                   first_amplitude_vpp: int, *, frequency_hz: int = 100) -> bool:
        with self._operation_lock:
            self._require_owner(owner)
            result = self.connection.send_sequence(
                pump_start_commands(
                    driver_index,
                    frequency_hz,
                    carrier_waveform,
                    owner.channel,
                    first_amplitude_vpp,
                ),
                timeout=self.ack_timeout,
                rollback_command=pump_state_command(owner.channel, False),
            )
            if result.success:
                self.frequency_state.set(driver_index, frequency_hz)
                return True
            # Startup failed. It is safe to release only when rollback was
            # confirmed. If OFF is unknown, retain the reservation.
            if result.rollback_succeeded:
                self.ownership.release(owner.channel, owner.token)
            raise RuntimeError(result.message or "Wave pump start was not acknowledged")

    def set_wave_amplitude(self, owner: ChannelOwnership, driver_index: int, amplitude_vpp: int) -> None:
        with self._operation_lock:
            self._require_owner(owner)
            # Wave timing is defined by the step duration. Requiring a blocking
            # ACK at every 20 ms step would make that timing non-deterministic.
            # The initial start and final OFF remain acknowledged transactions.
            self.connection.send(
                driver_amplitude_command(driver_index, owner.channel, amplitude_vpp)
            )

    def stop_wave(self, owner: ChannelOwnership) -> bool:
        with self._operation_lock:
            current = self.ownership.get(owner.channel)
            if current is None:
                return True
            self._require_owner(owner)
            if not getattr(self.connection, "is_open", False):
                self.ownership.force_release(owner.channel)
                return True
            result = self.connection.send_sequence(
                (pump_state_command(owner.channel, False),), timeout=self.ack_timeout
            )
            if result.success:
                self.ownership.release(owner.channel, owner.token)
                return True
            return False

    def force_release_after_disconnect(self) -> None:
        self.ownership.force_release_all()
