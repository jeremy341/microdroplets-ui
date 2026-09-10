# Hardware and Board Architecture

This document explains how FluidicStudio maps software concepts to the physical laboratory hardware. Read this before changing pump-driver rules, serial commands, sensor handling, or board configuration.

For the exact serial command/ACK/reply lifecycle, see [Multiboard Communication](BOARD_COMMUNICATION.md). For manual pump and automatic Wave ownership/execution semantics, see [Pump + Wave Engine](PUMP_WAVE_ENGINE.md).

## 1. Physical topology

```text
Windows PC
├─ USB serial ── Bartels mp-Multiboard2
│  ├─ Driver 0 / F0 ── CH1, CH2, CH3, CH4
│  ├─ Driver 1 / F1 ── CH5
│  ├─ Driver 2 / F2 ── CH6
│  └─ Sensor interface / firmware stream
└─ USB video ── Dino-Lite camera
   ├─ OpenCV / DirectShow: preview and frames
   └─ DNX64: Dino-Lite-specific controls
```

The Multiboard and camera are independent USB devices. A camera problem is not automatically a board problem, and a board disconnect does not imply that the camera runtime must stop.

## 2. Multiboard serial boundary

The application uses the board through `backend/serial_manager.py` and command builders/parsers in `backend/protocol.py`.

Current serial framing:

```text
115200 baud
8 data bits
no parity
1 stop bit
ASCII commands
CRLF termination
```

UI pages should not construct raw serial strings. Add or change command syntax in `backend/protocol.py`, then use the backend service that owns that hardware action.

## 3. Pump-driver grouping

The fixed logical groups are defined by `backend/driver_capabilities.py`:

| Driver domain | Channels | Shared frequency command |
| --- | --- | --- |
| Driver 0 | CH1–CH4 | F0 |
| Driver 1 | CH5 | F1 |
| Driver 2 | CH6 | F2 |

A shared driver does **not** mean only one channel may run. For CH1–CH4:

```text
Shared by Driver 0:
- frequency
- carrier/signal mode when supported

Independent per channel:
- amplitude (Vpp)
- ON/OFF
- manual-vs-wave ownership
```

Therefore CH1 and CH2 may run simultaneously at different amplitudes while still sharing F0.

## 4. Configured driver types

`data/driver_config.json` is the authoritative software configuration for the physical modules expected to be installed.

The shipped file is:

```json
{
  "schema_version": 1,
  "drivers": {
    "ch1_4": "highdriver4",
    "ch5": "lowdriver",
    "ch6": "mp_driver"
  }
}
```

The allowed configuration model is intentionally narrow:

- CH1–CH4: `highdriver4`
- CH5: `lowdriver` or `highdriver`
- CH6: `mp_driver`

Changing JSON does not change the hardware. Power the board down before changing a physical driver, update the file to match the real module, and restart FluidicStudio because the configuration is cached once per process.

## 5. Capability model

Driver limits live in `backend/driver_capabilities.py`. Pages should consume those capabilities rather than duplicate ranges.

| Driver type | UI amplitude | UI frequency | Carrier mode | Notes |
| --- | ---: | ---: | --- | --- |
| mp-Highdriver4 | 10–250 Vpp | 50–800 Hz | Yes | CH1–CH4, 5-bit amplitude quantization model |
| mp-Highdriver | 10–250 Vpp | 50–800 Hz | Yes | Allowed on CH5 configuration |
| mp-Lowdriver | 0–150 Vpp | 8–800 Hz | No | Hardware frequency range is wider than the app policy range |
| mp-Driver | 85–250 Vpp | 25–226 Hz | No | CH6 |

These are FluidicStudio capability/policy values. Do not describe them as measured pump flow-rate limits.

## 6. Pump ownership and interlocks

`backend/channel_ownership.py` owns the rule that one physical channel cannot be controlled manually and by a generated waveform at the same time.

Conceptually:

```text
CH2
├─ FREE
├─ MANUAL
└─ WAVEFORM
```

Ownership is **per channel**, not per driver. Examples:

```text
CH1 manual + CH2 manual       valid
CH1 manual + CH2 waveform     valid
CH1 waveform + CH1 manual     invalid
```

`backend/pump_control.py` is the hardware boundary used by both Pumps and Wave. Do not add a second Workspace-only ownership system.

## 7. Shared driver frequency versus carrier mode

Frequency is a live driver-level property. Changing F0 changes the frequency seen by CH1–CH4, even if one or more of those channels are currently running.

Carrier/signal mode is intentionally stricter. The backend changes it only when the complete driver group is idle, and only for driver types that advertise carrier-waveform support.

This distinction is deliberate and must remain consistent in Pumps, Wave, Workspace, sessions, and tests.

## 8. Sensor data path

Sensor command definitions and reply parsing live in `backend/protocol.py`. Parsed sensor events are produced by the serial layer and distributed to views through the existing sensor data path.

For liquid flow, the firmware value arrives in mL/min and is normalized **once** at the protocol boundary to FluidicStudio's display/logging unit:

```text
mL/min from firmware
        × 1000 once
        ↓
µL/min inside FluidicStudio
```

Do not apply another ×1000 conversion in UI, logging, Workspace, or Analytics.

The current sensor UI also has definitions for pressure, gas flow, and analog channels, but availability depends on what the connected board actually reports.

## 9. Camera boundary

The Dino-Lite does not run through the Multiboard. FluidicStudio deliberately separates:

- frame acquisition through OpenCV/DirectShow;
- Dino-Lite controls through DNX64 when available.

See [Camera Engine](CAMERA_ENGINE.md) for the runtime design.

## 10. Board configuration versus future detection

Today:

```text
data/driver_config.json = authoritative
```

The experimental `Driver:` fingerprint returned by the Multiboard is not yet trusted as a universal driver map. Read [Driver Detection](DRIVER_DETECTION.md) before changing this policy.

## 11. Where to change hardware behavior

| Change | Primary location |
| --- | --- |
| serial command syntax / reply parsing | `backend/protocol.py` |
| connection and sensor streaming | `backend/serial_manager.py` |
| driver grouping/ranges | `backend/driver_capabilities.py` |
| configured physical modules | `data/driver_config.json` / `backend/driver_config.py` |
| manual/wave channel ownership | `backend/channel_ownership.py` |
| acknowledged pump operations | `backend/pump_control.py` |
| waveform execution | `backend/wave_execution.py` |
| camera hardware boundary | `backend/camera_service.py` |

Keep hardware rules at these boundaries. Pages should present state, validate user input through shared capability data, and call the backend.
