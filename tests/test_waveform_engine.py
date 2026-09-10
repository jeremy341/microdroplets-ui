import pytest

from backend.waveform_engine import (
    MIN_STEP_DURATION_MS,
    WaveformDefinition,
    WaveformRunner,
    check_compatibility,
    generate_cycle,
    generate_steps,
    highdriver4_level_for_vpp,
    highdriver4_representable_commands,
    validate_definition,
    timing_samples_per_cycle,
    waveform_stats,
)


def definition(**changes):
    values = dict(
        id="test",
        name="Mixing Slow",
        template="Triangle",
        min_vpp=80,
        max_vpp=180,
        increment_vpp=10,
        samples_per_cycle=32,
        step_duration_ms=100,
        cycle_duration_ms=2000,
        cycles=5,
    )
    values.update(changes)
    return WaveformDefinition(**values)


def test_triangle_matches_expected_requested_step_sequence():
    cycle = generate_cycle(definition())
    assert [step.amplitude_vpp for step in cycle] == [
        80, 90, 100, 110, 120, 130, 140, 150, 160, 170,
        180, 170, 160, 150, 140, 130, 120, 110, 100, 90,
    ]


def test_triangle_stats_match_reference_case_without_hardware_quantization():
    stats = waveform_stats(definition())
    assert stats.steps_per_cycle == 20
    assert stats.total_steps == 100
    assert stats.cycle_duration_ms == 2000
    assert stats.total_duration_ms == 10000
    assert stats.wave_frequency_hz == 0.5


def test_sine_uses_cycle_and_step_duration_not_editable_sample_count():
    wave = definition(
        template="Sine",
        samples_per_cycle=4,  # legacy field must no longer control timing
        cycle_duration_ms=10_000,
        step_duration_ms=20,
        cycles=1,
    )
    assert timing_samples_per_cycle(wave) == 500
    cycle = generate_cycle(wave)
    values = [step.amplitude_vpp for step in cycle]
    assert min(values) == 80
    assert max(values) == 180
    assert all(80 <= value <= 180 for value in values)
    assert sum(step.duration_ms for step in cycle) == 10_000

    same_timing = definition(
        template="Sine",
        samples_per_cycle=512,
        cycle_duration_ms=10_000,
        step_duration_ms=20,
        cycles=1,
    )
    assert generate_cycle(same_timing) == cycle


def test_cycle_duration_and_step_duration_are_independent_timing_inputs():
    short_cycle = definition(
        template="Sine", cycle_duration_ms=10_000, step_duration_ms=20, cycles=1
    )
    long_cycle = definition(
        template="Sine", cycle_duration_ms=20_000, step_duration_ms=20, cycles=1
    )
    coarser = definition(
        template="Sine", cycle_duration_ms=10_000, step_duration_ms=100, cycles=1
    )
    assert short_cycle.step_duration_ms == long_cycle.step_duration_ms == 20
    assert timing_samples_per_cycle(short_cycle) == 500
    assert timing_samples_per_cycle(long_cycle) == 1000
    assert timing_samples_per_cycle(coarser) == 100
    assert waveform_stats(short_cycle).cycle_duration_ms == 10_000
    assert waveform_stats(long_cycle).cycle_duration_ms == 20_000


def test_non_divisible_cycle_keeps_exact_duration_without_unsafe_tiny_remainder():
    wave = definition(
        template="Sine", cycle_duration_ms=10_000, step_duration_ms=30, cycles=1
    )
    cycle = generate_cycle(wave)
    assert sum(step.duration_ms for step in cycle) == 10_000
    assert all(step.duration_ms >= MIN_STEP_DURATION_MS for step in cycle)


def test_square_is_two_hold_levels_per_cycle():
    cycle = generate_cycle(definition(template="Square"))
    assert [step.amplitude_vpp for step in cycle] == [80, 180]


def test_sawtooth_resets_only_at_cycle_boundary():
    cycle = generate_cycle(definition(template="Sawtooth", increment_vpp=25))
    assert [step.amplitude_vpp for step in cycle] == [80, 105, 130, 155, 180]


def test_step_duration_has_conservative_20ms_mvp_floor():
    invalid = definition(step_duration_ms=MIN_STEP_DURATION_MS - 1)
    valid, reason = validate_definition(invalid)
    assert valid is False
    assert "20" in reason

    valid, reason = validate_definition(definition(step_duration_ms=MIN_STEP_DURATION_MS))
    assert valid is True
    assert reason == ""


def test_channel_compatibility_reuses_verified_limits_without_clamping():
    wave = definition(min_vpp=80, max_vpp=180)
    result = check_compatibility(wave, 2)
    assert result.valid is True
    assert "5-bit amplitude quantization active" in result.reason

    result = check_compatibility(wave, 6)
    assert result.valid is False
    assert result.allowed_min_vpp == 85
    assert result.required_min_vpp == 80
    assert "85–250 Vpp" in result.reason
    assert wave.min_vpp == 80


def test_default_lowdriver_ch5_wave_uses_configured_limits():
    compatible_wave = definition(min_vpp=20, max_vpp=100, increment_vpp=10)
    result = check_compatibility(compatible_wave, 5)
    assert result.valid is True
    assert result.allowed_min_vpp == 0
    assert result.allowed_max_vpp == 150
    assert "mp-Lowdriver" in result.reason
    assert "8-bit internal amplitude control" in result.reason

    incompatible_wave = definition(min_vpp=80, max_vpp=180)
    result = check_compatibility(incompatible_wave, 5)
    assert result.valid is False
    assert result.allowed_max_vpp == 150
    assert "Allowed 0–150 Vpp" in result.reason


def test_highdriver4_uses_discrete_five_bit_command_levels():
    commands = highdriver4_representable_commands(80, 180)
    assert commands == (81, 89, 97, 105, 113, 121, 130, 138, 146, 154, 162, 170, 178)
    # Bartels' example conversion is Vpp * 31 / 250 stored in an integer byte.
    assert highdriver4_level_for_vpp(81) == 10
    assert highdriver4_level_for_vpp(178) == 22


def test_channel_aware_preview_steps_match_effective_highdriver4_commands():
    cycle = generate_cycle(definition(cycles=1), channel=2)
    values = [step.amplitude_vpp for step in cycle]
    assert values == [
        81, 89, 97, 113, 121, 130, 138, 146, 162, 170,
        178, 170, 162, 146, 138, 130, 121, 113, 97, 89,
    ]
    assert all(80 <= value <= 180 for value in values)


def test_too_narrow_highdriver4_range_is_incompatible_not_clamped():
    wave = definition(min_vpp=82, max_vpp=88, increment_vpp=1, step_duration_ms=20)
    result = check_compatibility(wave, 2)
    assert result.valid is False
    assert "two usable mp-Highdriver4 5-bit levels" in result.reason
    with pytest.raises(ValueError):
        generate_steps(wave, channel=2)


def test_generate_steps_repeats_cycle_without_mutating_requested_values():
    wave = definition(cycles=2)
    cycle = generate_cycle(wave)
    steps = generate_steps(wave)
    assert steps == cycle + cycle


def test_runner_sends_fixed_100hz_quantized_steps_and_off():
    commands = []
    finished = []
    runner = WaveformRunner(
        commands.append,
        on_finished=lambda ok, message: finished.append((ok, message)),
    )
    wave = definition(
        min_vpp=80,
        max_vpp=100,
        increment_vpp=20,
        step_duration_ms=20,
        cycle_duration_ms=100,
        cycles=1,
    )
    runner.start(wave, 2, carrier_waveform="Sinus")
    runner.join(1.0)
    # Effective CH2 commands are quantized to Highdriver4 levels inside the
    # requested 80-100 Vpp range.
    assert commands[:4] == ["F0=100", "CS0=0", "P2V81", "P2ON"]
    assert "P2V97" in commands
    assert commands[-1] == "P2OFF"
    assert finished and finished[-1][0] is True


def test_runner_executes_default_lowdriver_ch5_without_cs1_or_highdriver_quantization():
    commands = []
    finished = []
    runner = WaveformRunner(
        commands.append,
        on_finished=lambda ok, message: finished.append((ok, message)),
    )
    wave = definition(
        min_vpp=20,
        max_vpp=40,
        increment_vpp=10,
        step_duration_ms=20,
        cycle_duration_ms=100,
        cycles=1,
        driver_frequency_hz=175,
    )
    runner.start(wave, 5, carrier_waveform="Sinus")
    runner.join(1.0)
    assert commands[:3] == ["F1=175", "P5V20", "P5ON"]
    assert not any(command.startswith("CS1=") for command in commands)
    assert "P5V30" in commands or "P5V40" in commands
    assert commands[-1] == "P5OFF"
    assert finished and finished[-1][0] is True
