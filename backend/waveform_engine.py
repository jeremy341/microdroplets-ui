"""Waveform generation, validation, compatibility and execution helpers.

The waveform engine modulates pump amplitude over time and stores an initial
physical driver carrier frequency as part of each saved definition. The runtime
frequency is a live driver property and can be adjusted without restarting the
amplitude timeline.

Waveform safety policy for the configured hardware:
* CH1-CH4: mp-Highdriver4, 10-250 Vpp, 5-bit amplitude control.
* CH5: mp-Lowdriver by default, or mp-Highdriver when configured in JSON.
* CH6: mp-Driver, 85-250 Vpp.
* Step duration: minimum 20 ms for the PC -> USB -> Multiboard MVP path.

The 20 ms limit is a conservative FluidicStudio policy, not a Bartels-specified
serial command minimum.  It intentionally gives the 100 Hz carrier at least two
full periods per amplitude hold and avoids pretending that 1 ms Windows/USB
command timing is deterministic.

See ``docs/DEVELOPER_GUIDE.md`` for driver configuration, shared frequency
domains, Highdriver4 quantization, channel ownership and stop semantics.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import math
import threading
from time import monotonic
from typing import Callable, Iterable

from backend.driver_capabilities import (
    capabilities_for_channel,
    driver_index_for_channel,
)
from backend.protocol import (
    driver_amplitude_command,
    pump_start_commands,
    pump_state_command,
)


DRIVER_FREQUENCY_HZ = 100
MIN_STEP_DURATION_MS = 20
MAX_STEP_DURATION_MS = 60_000
MIN_CYCLE_DURATION_MS = 100
MAX_CYCLE_DURATION_MS = 600_000
MAX_LOGICAL_SAMPLES = 1_000_000
SUPPORTED_TEMPLATES = ("Triangle", "Sine", "Sawtooth", "Square")
WAVEFORM_SUPPORTED_CHANNELS = frozenset(range(1, 7))
HIGHDRIVER4_MAX_LEVEL = 31  # 5-bit setting: 0..31
HIGHDRIVER4_FULL_SCALE_VPP = 250


@dataclass(frozen=True)
class WaveformDefinition:
    id: str
    name: str
    template: str = "Triangle"
    min_vpp: int = 80
    max_vpp: int = 180
    increment_vpp: int = 10
    samples_per_cycle: int = 32
    step_duration_ms: int = 100
    cycle_duration_ms: int = 2000
    cycles: int = 5
    driver_frequency_hz: int = DRIVER_FREQUENCY_HZ

    def snapshot(self) -> "WaveformDefinition":
        """Return an immutable execution snapshot."""

        return replace(self)


@dataclass(frozen=True)
class WaveStep:
    # This is the integer Vpp command that will actually be sent to the board.
    amplitude_vpp: int
    duration_ms: int


@dataclass(frozen=True)
class CompatibilityResult:
    valid: bool
    channel: int
    required_min_vpp: int
    required_max_vpp: int
    allowed_min_vpp: int | None
    allowed_max_vpp: int | None
    reason: str = ""


@dataclass(frozen=True)
class WaveformStats:
    steps_per_cycle: int
    total_steps: int
    cycle_duration_ms: int
    total_duration_ms: int
    wave_frequency_hz: float


def driver_for_channel(channel: int) -> int:
    """Return the physical Multiboard driver domain for a pump channel."""

    return driver_index_for_channel(channel)


def channel_limits(channel: int) -> tuple[int, int]:
    """Return configured amplitude limits for any supported Wave target."""

    if channel not in WAVEFORM_SUPPORTED_CHANNELS:
        raise ValueError(f"Unsupported waveform channel: CH{channel}")
    return capabilities_for_channel(channel).amplitude_limits


def highdriver4_level_for_vpp(amplitude_vpp: int) -> int:
    """Mirror Bartels' Highdriver4 example conversion to the 5-bit level.

    Their example calculates ``Vpp * 31 / 250`` and stores the result in an
    unsigned byte, which truncates the fractional part.  This helper is used
    only to model the discrete output setting; the physical output still has
    normal component/device tolerances and is not a calibrated voltage meter.
    """

    if not 0 <= int(amplitude_vpp) <= HIGHDRIVER4_FULL_SCALE_VPP:
        raise ValueError("Highdriver4 amplitude must be between 0 and 250 Vpp")
    return max(
        0,
        min(
            HIGHDRIVER4_MAX_LEVEL,
            int(float(amplitude_vpp) * HIGHDRIVER4_MAX_LEVEL / HIGHDRIVER4_FULL_SCALE_VPP),
        ),
    )


def highdriver4_command_for_level(level: int) -> int:
    """Return a canonical integer Vpp command that maps to ``level``.

    ``ceil`` is important: using the rounded ideal voltage can fall just below
    the firmware threshold and map back to the previous 5-bit level.
    """

    if not 0 <= int(level) <= HIGHDRIVER4_MAX_LEVEL:
        raise ValueError("Highdriver4 level must be between 0 and 31")
    if level == 0:
        return 0
    return int(
        math.ceil(
            float(level) * HIGHDRIVER4_FULL_SCALE_VPP / HIGHDRIVER4_MAX_LEVEL
        )
    )


def highdriver4_representable_commands(min_vpp: int, max_vpp: int) -> tuple[int, ...]:
    """Canonical CH1-CH4 commands that stay inside the requested Vpp range."""

    allowed_min, allowed_max = capabilities_for_channel(1).amplitude_limits
    low = max(int(min_vpp), allowed_min)
    high = min(int(max_vpp), allowed_max)
    if low > high:
        return ()

    commands: list[int] = []
    for level in range(1, HIGHDRIVER4_MAX_LEVEL + 1):
        command = max(allowed_min, highdriver4_command_for_level(level))
        command = min(allowed_max, command)
        if low <= command <= high and command not in commands:
            commands.append(command)
    return tuple(commands)


def _logical_step_durations(cycle_duration_ms: int, step_duration_ms: int) -> list[int]:
    """Return update intervals that preserve the requested cycle duration.

    ``step_duration_ms`` is the normal update interval.  When the cycle length
    is not an exact multiple, only the final interval is adjusted.  A tiny
    remainder below the safe 20 ms floor is folded into the preceding hold so
    the runner never creates an unsafe sub-minimum hardware interval.
    """

    cycle_duration_ms = int(cycle_duration_ms)
    step_duration_ms = int(step_duration_ms)
    if cycle_duration_ms <= 0 or step_duration_ms <= 0:
        return []
    if cycle_duration_ms <= step_duration_ms:
        return [cycle_duration_ms]

    full_steps, remainder = divmod(cycle_duration_ms, step_duration_ms)
    durations = [step_duration_ms] * full_steps
    if remainder:
        if remainder >= MIN_STEP_DURATION_MS:
            durations.append(remainder)
        elif durations:
            durations[-1] += remainder
        else:
            durations.append(remainder)
    return durations


def timing_samples_per_cycle(definition: WaveformDefinition) -> int:
    """Number of logical time samples implied by cycle + step duration."""

    return len(
        _logical_step_durations(
            definition.cycle_duration_ms,
            definition.step_duration_ms,
        )
    )


def validate_definition(definition: WaveformDefinition) -> tuple[bool, str]:
    name = definition.name.strip()
    if not name:
        return False, "Waveform name cannot be empty."
    if len(name) > 50:
        return False, "Waveform name must be 50 characters or fewer."
    if definition.template not in SUPPORTED_TEMPLATES:
        return False, f"Unsupported template: {definition.template}."
    if definition.min_vpp < 0 or definition.max_vpp > 250:
        return False, "Amplitude must stay within the global 0–250 Vpp range."
    if definition.min_vpp >= definition.max_vpp:
        return False, "Minimum amplitude must be lower than maximum amplitude."
    if not MIN_STEP_DURATION_MS <= definition.step_duration_ms <= MAX_STEP_DURATION_MS:
        return False, (
            f"Step duration must be between {MIN_STEP_DURATION_MS} and "
            f"{MAX_STEP_DURATION_MS} ms."
        )
    if not MIN_CYCLE_DURATION_MS <= definition.cycle_duration_ms <= MAX_CYCLE_DURATION_MS:
        return False, (
            f"Cycle duration must be between {MIN_CYCLE_DURATION_MS / 1000:g} and "
            f"{MAX_CYCLE_DURATION_MS / 1000:g} seconds."
        )
    if not 8 <= int(definition.driver_frequency_hz) <= 800:
        return False, "Driver frequency must be between 8 and 800 Hz."
    if definition.cycles < 1:
        return False, "Cycles must be at least 1."
    if definition.template in ("Triangle", "Sawtooth"):
        if definition.increment_vpp < 1:
            return False, "Increment must be at least 1 Vpp."
        if definition.increment_vpp > definition.max_vpp - definition.min_vpp:
            return False, "Increment cannot be larger than the amplitude range."

    samples = timing_samples_per_cycle(definition)
    minimum_samples = 4 if definition.template in ("Triangle", "Sine") else 2
    if samples < minimum_samples:
        return False, (
            f"{definition.template} requires at least {minimum_samples} timing samples per "
            "cycle. Increase Cycle duration or reduce Step duration."
        )
    if samples * int(definition.cycles) > MAX_LOGICAL_SAMPLES:
        return False, (
            "Waveform contains too many timing samples. Reduce Cycle duration, "
            "increase Step duration, or reduce Cycles."
        )
    return True, ""


def _snap_to_increment(value: float, definition: WaveformDefinition) -> int:
    """Preserve Triangle/Sawtooth Increment as requested Vpp granularity."""

    offset = max(0.0, float(value) - float(definition.min_vpp))
    steps = round(offset / float(definition.increment_vpp))
    snapped = definition.min_vpp + steps * definition.increment_vpp
    return max(definition.min_vpp, min(definition.max_vpp, int(snapped)))


def _requested_cycle(definition: WaveformDefinition) -> list[WaveStep]:
    """Generate one time-based cycle from independent cycle/step durations."""

    durations = _logical_step_durations(
        definition.cycle_duration_ms,
        definition.step_duration_ms,
    )
    count = len(durations)
    template = definition.template
    center = (definition.min_vpp + definition.max_vpp) / 2.0
    radius = (definition.max_vpp - definition.min_vpp) / 2.0
    span = float(definition.max_vpp - definition.min_vpp)

    amplitudes: list[int] = []
    for index in range(count):
        phase = index / float(count)
        if template == "Triangle":
            normalized = 2.0 * phase if phase < 0.5 else 2.0 * (1.0 - phase)
            value = definition.min_vpp + span * normalized
            amplitude = _snap_to_increment(value, definition)
        elif template == "Sawtooth":
            # Include the requested maximum at the final sample before the
            # cycle boundary resets to minimum.
            normalized = index / float(max(1, count - 1))
            value = definition.min_vpp + span * normalized
            amplitude = _snap_to_increment(value, definition)
        elif template == "Square":
            amplitude = definition.min_vpp if phase < 0.5 else definition.max_vpp
        else:  # Sine
            value = center - radius * math.cos(2.0 * math.pi * phase)
            amplitude = int(round(value))
        amplitudes.append(int(amplitude))

    requested = [
        WaveStep(amplitude, duration)
        for amplitude, duration in zip(amplitudes, durations)
    ]
    # Repeated requested levels are physically identical holds.  Merge them
    # before serial execution while preserving exact cycle timing.
    return _merge_adjacent_equal_steps(requested)


def _merge_adjacent_equal_steps(steps: Iterable[WaveStep]) -> list[WaveStep]:
    """Merge equal consecutive driver settings without changing total time."""

    merged: list[WaveStep] = []
    for step in steps:
        if merged and merged[-1].amplitude_vpp == step.amplitude_vpp:
            previous = merged[-1]
            merged[-1] = WaveStep(
                previous.amplitude_vpp,
                previous.duration_ms + step.duration_ms,
            )
        else:
            merged.append(step)
    return merged


def _quantize_highdriver4_steps(
    definition: WaveformDefinition,
    steps: Iterable[WaveStep],
) -> list[WaveStep]:
    representable = highdriver4_representable_commands(
        definition.min_vpp,
        definition.max_vpp,
    )
    if len(representable) < 2:
        raise ValueError(
            "The selected Vpp range does not contain at least two usable "
            "mp-Highdriver4 5-bit amplitude levels."
        )

    quantized: list[WaveStep] = []
    for step in steps:
        command_vpp = min(
            representable,
            key=lambda value: (abs(value - step.amplitude_vpp), value),
        )
        quantized.append(WaveStep(command_vpp, step.duration_ms))
    return _merge_adjacent_equal_steps(quantized)


def generate_cycle(
    definition: WaveformDefinition,
    channel: int | None = None,
) -> list[WaveStep]:
    """Generate one cycle.

    Without a channel this returns the mathematical/requested integer sequence.
    Driver-aware generation returns the effective serial command sequence.
    mp-Highdriver4 targets use the verified 5-bit quantization model; CH5 and
    CH6 currently keep the requested integer-Vpp Multiboard commands.
    """

    valid, reason = validate_definition(definition)
    if not valid:
        raise ValueError(reason)

    requested = _requested_cycle(definition)
    if channel is None:
        return requested

    compatibility = check_compatibility(definition, channel)
    if not compatibility.valid:
        raise ValueError(compatibility.reason)
    if capabilities_for_channel(channel).wave_quantization == "highdriver4_5bit":
        return _quantize_highdriver4_steps(definition, requested)
    return requested


def generate_steps(
    definition: WaveformDefinition,
    channel: int | None = None,
) -> list[WaveStep]:
    if channel is None:
        cycle = generate_cycle(definition)
        return _merge_adjacent_equal_steps(cycle * int(definition.cycles))

    # Quantize the complete sequence so equal levels at cycle boundaries can
    # be merged into one hold while preserving total duration.
    compatibility = check_compatibility(definition, channel)
    if not compatibility.valid:
        raise ValueError(compatibility.reason)
    requested_cycle = _requested_cycle(definition)
    requested_full = _merge_adjacent_equal_steps(requested_cycle * int(definition.cycles))
    if capabilities_for_channel(channel).wave_quantization == "highdriver4_5bit":
        return _quantize_highdriver4_steps(definition, requested_full)
    return requested_full


def waveform_stats(
    definition: WaveformDefinition,
    channel: int | None = None,
) -> WaveformStats:
    cycle = generate_cycle(definition, channel=channel)
    cycle_duration_ms = sum(step.duration_ms for step in cycle)
    full_steps = generate_steps(definition, channel=channel)
    total_duration_ms = sum(step.duration_ms for step in full_steps)
    frequency = 1000.0 / cycle_duration_ms if cycle_duration_ms > 0 else 0.0
    return WaveformStats(
        steps_per_cycle=len(cycle),
        total_steps=len(full_steps),
        cycle_duration_ms=cycle_duration_ms,
        total_duration_ms=total_duration_ms,
        wave_frequency_hz=frequency,
    )


def check_compatibility(definition: WaveformDefinition, channel: int) -> CompatibilityResult:
    try:
        allowed_min, allowed_max = channel_limits(channel)
    except ValueError as exc:
        return CompatibilityResult(
            False,
            channel,
            definition.min_vpp,
            definition.max_vpp,
            None,
            None,
            str(exc),
        )

    valid_definition, reason = validate_definition(definition)
    if not valid_definition:
        return CompatibilityResult(
            False,
            channel,
            definition.min_vpp,
            definition.max_vpp,
            allowed_min,
            allowed_max,
            reason,
        )

    compatible = (
        definition.min_vpp >= allowed_min
        and definition.max_vpp <= allowed_max
    )
    capabilities = capabilities_for_channel(channel)
    frequency_min, frequency_max = capabilities.frequency_limits
    frequency_compatible = frequency_min <= definition.driver_frequency_hz <= frequency_max
    if compatible and not frequency_compatible:
        compatible = False
        reason = (
            f"Incompatible with CH{channel} · Requires {definition.driver_frequency_hz} Hz · "
            f"Allowed {frequency_min}–{frequency_max} Hz"
        )
    elif not compatible:
        reason = (
            f"Incompatible with CH{channel} · Requires "
            f"{definition.min_vpp}–{definition.max_vpp} Vpp · Allowed "
            f"{allowed_min}–{allowed_max} Vpp"
        )
    elif capabilities.wave_quantization == "highdriver4_5bit":
        representable = highdriver4_representable_commands(
            definition.min_vpp,
            definition.max_vpp,
        )
        if len(representable) < 2:
            compatible = False
            reason = (
                f"Incompatible with CH{channel} · The requested "
                f"{definition.min_vpp}–{definition.max_vpp} Vpp range does "
                "not contain two usable mp-Highdriver4 5-bit levels"
            )
        else:
            reason = (
                f"Compatible with CH{channel} · Allowed range "
                f"{allowed_min}–{allowed_max} Vpp · 5-bit amplitude "
                "quantization active"
            )
    else:
        resolution = (
            " · 8-bit internal amplitude control"
            if capabilities.amplitude_control_bits == 8 else ""
        )
        reason = (
            f"Compatible with CH{channel} · {capabilities.display_name} · Allowed range "
            f"{allowed_min}–{allowed_max} Vpp{resolution}"
        )

    return CompatibilityResult(
        compatible,
        channel,
        definition.min_vpp,
        definition.max_vpp,
        allowed_min,
        allowed_max,
        reason,
    )


class WaveformRunner:
    """Execute an immutable waveform snapshot on one physical channel.

    The runner deliberately owns its own worker thread.  Waiting between
    amplitude commands therefore never blocks Qt's GUI thread.  ``stop()``
    interrupts the current wait immediately and always attempts to switch the
    channel OFF before releasing ownership.
    """

    def __init__(
        self,
        send_command: Callable[[str], object],
        *,
        on_step: Callable[[int, WaveStep], None] | None = None,
        on_finished: Callable[[bool, str], None] | None = None,
    ) -> None:
        self._send_command = send_command
        self._on_step = on_step
        self._on_finished = on_finished
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._channel: int | None = None
        self._lock = threading.Lock()

    @property
    def is_running(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    def start(
        self,
        definition: WaveformDefinition,
        channel: int,
        *,
        carrier_waveform: str = "Sinus",
    ) -> None:
        with self._lock:
            if self.is_running:
                raise RuntimeError("A waveform is already running.")
            compatibility = check_compatibility(definition, channel)
            if not compatibility.valid:
                raise ValueError(compatibility.reason)
            snapshot = definition.snapshot()
            # Channel-aware generation is the safety boundary: the preview,
            # Generated Steps table and runner all consume the same effective
            # command sequence.
            steps = tuple(generate_steps(snapshot, channel=channel))
            self._stop_event.clear()
            self._channel = channel
            self._thread = threading.Thread(
                target=self._run,
                args=(snapshot, channel, steps, carrier_waveform),
                name=f"WaveformRunner-CH{channel}",
                daemon=True,
            )
            self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()

    def join(self, timeout: float | None = None) -> None:
        thread = self._thread
        if thread is not None:
            thread.join(timeout)

    def _run(
        self,
        definition: WaveformDefinition,
        channel: int,
        steps: Iterable[WaveStep],
        carrier_waveform: str,
    ) -> None:
        # The configure -> timed Vpp steps -> OFF sequence follows the shared
        # protocol/safety rules documented in docs/DEVELOPER_GUIDE.md.
        completed = False
        message = "Stopped"
        try:
            step_list = list(steps)
            if not step_list:
                raise ValueError("Waveform contains no generated steps.")
            # A stop request that arrived before the thread started must not
            # still execute the start-up commands; that would pulse the pump
            # ON for the duration of the sequence before the finally-OFF.
            if self._stop_event.is_set():
                return
            driver_index = driver_for_channel(channel)
            first_step = step_list[0]
            for command in pump_start_commands(
                driver_index,
                definition.driver_frequency_hz,
                carrier_waveform,
                channel,
                first_step.amplitude_vpp,
            ):
                # A stop request arriving mid-start-up must not continue
                # issuing ON commands; the finally block still sends OFF.
                if self._stop_event.is_set():
                    break
                self._send_command(command)

            # Holds are scheduled against an absolute timeline: send latency
            # and OS timer overshoot must not accumulate across steps, or the
            # effective wave frequency drifts far below the configured value.
            next_deadline = monotonic()
            for index, step in enumerate(step_list, start=1):
                if self._stop_event.is_set():
                    break
                # The first Vpp is already part of the deterministic start
                # sequence; avoid sending it twice.
                if index > 1:
                    self._send_command(
                        driver_amplitude_command(
                            driver_index,
                            channel,
                            step.amplitude_vpp,
                        )
                    )
                if self._on_step is not None:
                    self._on_step(index, step)
                next_deadline += step.duration_ms / 1000.0
                remaining = next_deadline - monotonic()
                if self._stop_event.wait(max(0.0, remaining)):
                    break
            else:
                completed = True
                message = "Completed"
        except Exception as exc:  # pragma: no cover - hardware boundary
            message = str(exc)
        finally:
            try:
                self._send_command(pump_state_command(channel, False))
            except Exception:
                pass
            self._channel = None
            if self._on_finished is not None:
                self._on_finished(completed, message)
