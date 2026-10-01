# Pump Control and Wave Execution Engine

This document describes the backend rules shared by the Pumps page, Wave page
and their Workspace views.

Main files:

```text
backend/channel_ownership.py
backend/pump_control.py
backend/driver_capabilities.py
backend/driver_frequency_state.py
backend/protocol.py
backend/waveform_engine.py
backend/wave_execution.py
backend/waveform_library.py
```

## 1. Physical resource model

Current channel grouping:

```text
Driver 0 / F0
├─ CH1
├─ CH2
├─ CH3
└─ CH4

Driver 1 / F1
└─ CH5

Driver 2 / F2
└─ CH6
```

The single most important rule is:

```text
same driver != same channel
```

For CH1–CH4:

### Shared driver-level state

- frequency (`F0`);
- carrier/signal waveform when supported.

### Independent channel-level state

- amplitude (`P1V...`, `P2V...`, etc.);
- ON/OFF;
- manual-vs-Wave ownership.

Therefore these are valid at the ownership level:

```text
CH1 manual ON + CH2 manual ON
CH1 manual ON + CH2 Wave
different channels can hold separate Wave/manual ownership
```

If two active channels share one driver, any driver-level frequency/carrier configuration still affects both. Ownership independence does not create independent F0/CS0 hardware.

This is not valid:

```text
CH1 manual + CH1 Wave
```

## 2. Driver configuration and capabilities

`data/driver_config.json` describes the expected physical driver types.

Current fixed/allowed groups:

```text
CH1–CH4 → mp-Highdriver4
CH5     → mp-Lowdriver OR mp-Highdriver
CH6     → mp-Driver
```

`backend/driver_capabilities.py` converts that configuration into:

- amplitude range;
- normal FluidicStudio frequency range;
- hardware frequency range metadata;
- carrier-waveform support;
- amplitude quantization model.

UI pages should not invent their own limits.

## 3. Channel ownership

`ChannelOwnershipManager` answers one question:

> Who currently has exclusive right to command this physical channel?

Owner kinds:

```text
manual
waveform
free (absence of an owner)
```

A successful `claim()` returns a token. Only the same token may release that
claim. This prevents an old Wave worker finishing late from accidentally
releasing a newer owner's reservation.

Ownership is **per channel**, not per driver.

## 4. Manual pump startup

`PumpControlService.start_manual()`:

1. claims the channel as `manual`;
2. builds a configure-then-start command sequence;
3. sends the sequence with ACK requirements;
4. rolls back with only that channel's OFF if startup fails;
5. updates last acknowledged driver frequency on success.

Audit note: the current implementation claims ownership before every command
sequence input has been validated. An invalid manual-start request can therefore
leak a claim even though no output was started. Treat validation-before-claim as
a required invariant for future fixes.

Typical Highdriver4 sequence:

```text
F0=100
CS0=0
P1V120
P1ON
```

The startup transaction does not globally stop other channels on the same driver.

## 5. Manual pump stop

`stop_manual(channel)` sends:

```text
P<channel>OFF
```

and releases ownership only after the OFF is acknowledged.

If OFF is not confirmed, ownership remains and hardware state is marked unknown.
That is deliberate safety behavior.

## 6. Amplitude behavior

Amplitude is independent per channel.

When a manual pump is ON, `set_manual_amplitude()` requires the channel to be
owned by manual control and sends an acknowledged amplitude command.

The UI also allows an amplitude value to be **staged while OFF**. In that case
it is a RAM/UI configuration; it is sent as part of the next start sequence.

This is why OFF amplitude controls look muted in the Pumps UX but remain
editable.

## 7. Driver frequency behavior

Frequency is shared per physical driver and is deliberately **live**.

```text
set_driver_frequency(0, 150)
→ F0=150
→ affects CH1–CH4 together
```

It can be changed while manual pumps or Waves are active.

This means if Workspace shows CH1 and CH2, changing either card's frequency must
update the other because both display the same underlying `F0` value.

`DriverFrequencyState` stores the last acknowledged value so every view reads the
same state.

## 8. Carrier/signal waveform behavior

Carrier shape is also a shared driver setting, but unlike frequency it is not
changed live in the current policy.

`set_driver_waveform()` first verifies that **all channels on the driver are
idle**.

For CH1–CH4, if any of CH1/2/3/4 has manual or Wave ownership, an explicit live
carrier change through this service is blocked.

Startup sequences still include the already-selected/configured carrier command.
The UI/backend therefore needs to keep the driver carrier value synchronized so
a sibling channel startup does not accidentally request a contradictory carrier.
If future requirements need different carrier modes simultaneously on sibling
channels, that is not physically representable on one shared driver domain.

## 9. Wave definition vs Wave execution

These are separate concepts.

### `WaveformDefinition`

An immutable-style configuration containing fields such as:

- name/id;
- template;
- min/max Vpp;
- increment/sampling parameters;
- step duration;
- cycle duration;
- cycles;
- initial driver frequency.

### Runtime execution

`WaveExecutionService` owns running `WaveformRunner` instances and execution
state by channel.

Starting a Wave snapshots the definition so edits to a saved Wave cannot mutate
the already-running timeline.

## 10. Wave templates

Current generated templates:

```text
Triangle
Sine
Sawtooth
Square
```

`backend/waveform_engine.py` validates the definition and generates discrete
amplitude steps compatible with the selected channel's driver.

## 11. Wave timing policy

Current minimum amplitude step duration:

```text
20 ms
```

This is a conservative FluidicStudio policy for the Windows → USB serial →
Multiboard path, not a claim that the Bartels serial protocol itself specifies
20 ms as an absolute minimum.

The design intentionally avoids pretending that 1 ms command updates through a
normal Windows USB serial stack are deterministic.

## 12. Highdriver4 quantization

The Highdriver4 has a 5-bit amplitude setting model (31 nonzero steps in the
current modelling). FluidicStudio accepts integer Vpp commands but
`waveform_engine.py` models representable values so generated Waves do not
pretend every integer Vpp corresponds to a distinct physical level.

This matters particularly for automatically generated waveform steps.

## 13. Wave startup routing

`WaveformRunner` conceptually emits:

```text
frequency
carrier
first amplitude
ON
then timed amplitude changes
finally OFF
```

`WaveExecutionService` intercepts the startup portion. Instead of allowing the
runner to independently send each startup command, it calls
`PumpControlService.start_wave()` so ownership, ACKs and rollback semantics are
identical to manual pump startup.

## 14. Timed amplitude updates

After startup, Wave amplitude steps call:

```python
PumpControlService.set_wave_amplitude(...)
```

These timed step writes are intentionally non-blocking/unacknowledged at each
step. Waiting for a full serial ACK on every 20 ms step would destroy the timing
model.

The **initial start** and **final OFF** remain acknowledged safety boundaries.

## 15. Wave stop and failure semantics

At completion or stop, the runner tries the affected channel's OFF.

If OFF is not confirmed:

- execution state becomes error;
- ownership can remain held;
- UI should indicate that the channel is still locked/unknown;
- the user can retry stop.

`WaveExecutionService.stop_all()` is used during board/application shutdown and
performs a final per-channel OFF retry before higher-level shutdown can fall
back to global `POFF`. A final OFF that cannot be confirmed is never swallowed:
the channel's runtime state is set to `error` with an explicit message, so the
UI can show that the physical output state is unconfirmed.

The runner also refuses to keep powering up: a stop request that arrives before
or during the start-up sequence aborts the remaining ON commands, so a
"start then immediately stop" can no longer pulse the pump on.

Wave step holds are scheduled against an absolute timeline: each step waits
until the planned deadline rather than sleeping for its duration *after* the
previous write completed. Serial write latency and OS timer overshoot therefore
no longer accumulate, and the effective wave frequency matches the configured
`wave_frequency_hz` instead of drifting low.

Known lifecycle exception: `stop_wave()` can release ownership when the transport
is already closed, even though OFF was not confirmed. This contradicts the
normal unknown-state rule above and is a high-priority regression target. Do not
document or implement transport closure as proof of OFF.

## 16. Workspace parity

Pump Workspace and Wave Workspace must not implement their own ownership rules.
They read the same active board services.

Expected examples:

```text
Pumps page starts CH1 manually
→ Pump Workspace CH1 shows ON
→ Wave page/Workspace cannot start a Wave on CH1
→ CH2 remains usable

Wave Workspace starts CH2
→ full Wave page shows CH2 runtime
→ Pump views reject manual control of CH2
→ CH1 remains independent
```

## 17. UX rules that reflect backend semantics

### Pumps

- OFF amplitude: muted but editable (staged value).
- ON amplitude: active styling.
- frequency: live driver setting.
- carrier mode: disabled while any sibling on that driver is owned.
- manual/Wave conflicts: backend remains authoritative.

### Wave

When selected channel is under manual control:

```text
Test Waveform → disabled / “Pump in use”
```

When a Wave is running:

- channel selector and Wave-definition controls lock;
- Stop remains available;
- driver frequency remains live-editable by current policy.

## 18. Never do these

- Do not globally block CH2 because CH1 is active on the same Highdriver4.
- Do not give CH1 and CH2 separate `F0` values.
- Do not release ownership after an unconfirmed OFF.
- Do not use global `POFF` as a normal per-channel Stop button.
- Do not create separate ownership for Workspace.
- Do not wait for an ACK on every high-frequency Wave amplitude step.

## 19. Relevant tests

```text
test_channel_ownership.py
test_pump_control.py
test_live_frequency.py
test_waveform_engine.py
test_wave_frequency_control.py
test_waveform_library.py
test_workspace_pumps_smooth_controls.py
test_workspace_pumps_ux.py
test_workspace_ux_parity.py
test_workspace_tab_sync.py
```
