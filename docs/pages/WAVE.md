# Wave Page

The Wave page builds and executes time-varying pump-amplitude programs using the same hardware backend and channel ownership system as manual Pumps.


For execution snapshots, timing, ownership and startup/stop routing, see [Pump + Wave Engine](../PUMP_WAVE_ENGINE.md).

## What a waveform is

A saved waveform definition describes how amplitude changes over time. The editor supports generated forms such as:

- Triangle;
- Sine;
- Sawtooth;
- Square;
- manually configured step behavior through the existing definition model.

The editor exposes parameters such as:

- target channel;
- saved waveform;
- template;
- minimum amplitude;
- maximum amplitude;
- increment or a template-derived samples-per-cycle value;
- step/cycle timing;
- cycles;
- driver frequency.

## Generated steps are the hardware truth

The smooth mathematical preview is not what the serial driver receives directly. `backend/waveform_engine.py` turns a definition into discrete amplitude steps.

A driver can quantize/limit those values. Always inspect the generated steps/statistics when changing waveform generation.

The current UI offers four generated templates (Triangle, Sine, Sawtooth and
Square). Samples/steps are derived from the template and timing rules; they are
not an unrestricted editable raw sample buffer. Treat generated statistics as
read-only evidence of what will be sent.

## Saved waveform library

Saved definitions are stored in:

```text
data/waveforms.json
```

They are global definitions rather than being permanently attached to one physical channel. Compatibility is evaluated for the selected channel/driver.

## Ownership and manual Pumps

Wave execution claims the selected physical channel as `WAVEFORM` through the shared ownership manager.

If the same channel is currently manual:

```text
Test Waveform → disabled / "Pump in use"
```

A sibling channel on the same driver can still be independently used. Do not add a driver-wide “only one channel at a time” restriction.

## Running-state UX

While a waveform is running, the Wave editor locks the controls that would redefine the current execution, while Stop remains available.

Driver frequency remains a live shared property according to the current pump backend.

The running worker executes a generated step sequence rather than continuously re-reading arbitrary edited UI values.

## Timing

Wave step timing must account for serial/processing overhead. Extremely short step durations are not useful merely because a UI spinbox could represent them.

Keep generated waveform timing and serial architecture tests when changing performance-sensitive behavior.

## Workspace relationship

Wave Workspace edits the same Wave page draft/runtime. It is not a separate waveform model.

Changes in either view must stay synchronized for:

- selected channel;
- saved waveform/template;
- amplitudes;
- increment/samples;
- timing;
- cycles;
- driver frequency;
- manual-busy/running state;
- Stop availability.

## Relevant files

```text
ui/pages/Waveform.py
backend/waveform_engine.py
backend/waveform_library.py
backend/wave_execution.py
backend/pump_control.py
backend/channel_ownership.py
```

## Relevant tests

```text
test_waveform_engine.py
test_waveform_library.py
test_wave_execution.py
test_wave_frequency_control.py
test_channel_ownership.py
test_workspace_ux_parity.py
```

## Navigation

```text
Shift+4
```
