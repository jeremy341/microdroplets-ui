# FluidicStudio

FluidicStudio is a PyQt6 desktop application for controlling and observing a microfluidic experiment from one interface. It combines Bartels mp-Multiboard2 pump control, live sensor acquisition, generated pump waveforms, Dino-Lite camera capture, offline droplet video Analytics, session persistence, and a synchronized two-panel Workspace.

This release is structured as a **handoff-ready development project**: the important subsystem decisions are documented so a future developer should not need the original development chats or old V-series archives to understand how the application is supposed to behave.

## Current feature status

| Area | Status |
| --- | --- |
| Home dashboard | implemented |
| Manual Pumps | implemented |
| Sensors / CSV logging | implemented |
| Wave generation/execution | implemented |
| Dino-Lite Camera | implemented |
| Offline video Analytics | implemented |
| Two-panel Workspace | implemented |
| Session save/restore | implemented |
| Hardware diagnostics | implemented |
| Automatic pump-driver detection | experimental/research only |
| Valves | placeholder / future work |
| Still-image Analytics | future work |

## Quick start

Primary target: Windows 10/11, Python 3.11–3.13.

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python app.py
```

For development:

```powershell
python -m pip install -r requirements-dev.txt
pytest -q
```

## Hardware at a glance

```text
Windows PC
├─ USB serial → Bartels mp-Multiboard2
│  ├─ Driver 0 / F0 → CH1–CH4
│  ├─ Driver 1 / F1 → CH5
│  ├─ Driver 2 / F2 → CH6
│  └─ supported sensor streams
└─ USB video → Dino-Lite camera
```

The current software configuration is stored in:

```text
data/driver_config.json
```

The shipped layout expects:

```text
CH1–CH4  mp-Highdriver4
CH5      mp-Lowdriver
CH6      mp-Driver
```

CH1–CH4 can run as independent channels. They share driver-level frequency/carrier settings, while amplitude and ON/OFF remain per-channel.

Automatic driver detection is **not** authoritative in this release. See [Future Driver Detection](docs/DRIVER_DETECTION.md).

## Documentation map

The documentation is deliberately split by responsibility instead of placing everything in two giant files. For a task-oriented map, start with **[Documentation Index](docs/INDEX.md)**.

### Start here

- **[User Guide](docs/USER_GUIDE.md)** — installation, normal operation, workflow, troubleshooting and safety.
- **[Developer Guide](docs/DEVELOPER_GUIDE.md)** — codebase handoff, sources of truth, testing conventions and roadmap.

### Architecture / engines / hardware

- **[Software Architecture](docs/SOFTWARE_ARCHITECTURE.md)** — runtime assembly, board registry, shared services, threading and Workspace/full-page state ownership.
- **[Multiboard Communication](docs/BOARD_COMMUNICATION.md)** — UART framing, command/ACK transactions, production command table, reply parsing, flow-sensor handshake and shutdown.
- **[Hardware and Board Architecture](docs/HARDWARE_ARCHITECTURE.md)** — physical Multiboard topology and driver/channel domains.
- **[Hardware Limitations](docs/HARDWARE_LIMITATIONS.md)** — software limits, validation boundaries, shared settings and known hardware constraints.
- **[Pump + Wave Engine](docs/PUMP_WAVE_ENGINE.md)** — channel ownership, manual control, Wave execution, shared frequency/carrier semantics and timing.
- **[Sensor Pipeline](docs/SENSOR_PIPELINE.md)** — raw serial measurement → parser → history → UI/Workspace → CSV and volume integration.
- **[Camera Engine](docs/CAMERA_ENGINE.md)** — OpenCV/DNX64 split, shared runtime, workers, preview, exposure and capture.
- **[DNX64 Reference](docs/DNX64_REFERENCE.md)** — broad Dino-Lite API inventory, feature requirements, wrapper caveats and current integration status.
- **[Analytics Engine](docs/ANALYTICS_ENGINE.md)** — detector/tracker/event/calibration/cache/export architecture.
- **[Data and Sessions](docs/DATA_SESSIONS.md)** — runtime paths, persistent JSON, safe session restore and migration.
- **[UI Design System](docs/UI_DESIGN_SYSTEM.md)** — QSS/design tokens, control-state conventions, Workspace compact-layout rules and styling guidance.

### Development / future work

- **[Testing and Diagnostics](docs/TESTING_DIAGNOSTICS.md)** — test groups, safe hardware probes and troubleshooting order.
- **[Future Driver Detection](docs/DRIVER_DETECTION.md)** — existing fingerprint probes and the safe path toward automatic detection.
- **[Future Roadmap](docs/FUTURE_ROADMAP.md)** — known handoff backlog including valves, still-image Analytics, V66 benchmarking, DNX64 expansion, Workspace presets and packaging.

### One document per application page

- [Home](docs/pages/HOME.md)
- [Pumps](docs/pages/PUMPS.md)
- [Sensors](docs/pages/SENSORS.md)
- [Wave](docs/pages/WAVE.md)
- [Camera](docs/pages/CAMERA.md)
- [Analytics](docs/pages/ANALYTICS.md)
- [Workspace](docs/pages/WORKSPACE.md)
- [Valves](docs/pages/VALVES.md)

## Project structure

```text
app.py
    application shell, navigation, board lifecycle, sessions, shutdown

backend/
    protocol, serial connection, pumps, waves, sensors, camera, paths, sessions

backend/analytics/
    droplet video-analysis pipeline

backend/workspace/
    Workspace model/persistence

ui/pages/
    PyQt6 pages and compact Workspace panels

style.qss
    application visual styling

data/
    version-controlled configuration and waveform library

tools/
    safe hardware diagnostics and driver-fingerprint probes

tests/
    curated regression/unit/integration tests

user_data/
    generated captures, logs, sessions, diagnostics and Analytics files

vendor/dnx64/
    optional Dino-Lite vendor runtime/notices
```

## Core architecture rules

These are the decisions most likely to cause regressions if forgotten.

### Workspace is not a second backend

Pumps, Sensors, Wave and Camera Workspace panels are synchronized views of the same state/runtime as the corresponding full pages.

### Same driver does not mean same channel

For CH1–CH4:

```text
Shared:
- F0 frequency
- supported carrier/signal mode

Independent:
- channel amplitude
- channel ON/OFF
- manual/wave channel ownership
```

CH1 manual + CH2 Wave is a valid architecture. CH1 manual + CH1 Wave is blocked.

### Hardware commands are asynchronous/acknowledged

Do not treat a UI click or serial write as proof of physical state. Preserve pump acknowledgement/rollback behavior and do not overwrite actively edited sliders with periodic backend polling.

### Sensor units are normalized once

Liquid flow is converted from firmware mL/min to FluidicStudio µL/min once at the protocol boundary.

### One camera runtime

Camera page and Camera Workspace share the same OpenCV/DNX64 runtime and latest frame. Workspace must never open a second camera handle.

## Runtime data

Generated data belongs below `user_data/` and is excluded from normal source control:

```text
user_data/captures/
user_data/sensor_logs/
user_data/sessions/
user_data/diagnostics/
user_data/exports/
user_data/analytics/
```

Do not commit real experiment recordings/logs just to reproduce an issue. Keep small anonymized fixtures only when a test genuinely requires them.

## Dino-Lite / DNX64

Basic video preview uses OpenCV/DirectShow. Dino-Lite-specific controls use DNX64 when available.

The product UI exposes brightness, exposure and **LED ON/OFF**. LED intensity/FLC is deliberately not exposed because it was not sufficiently validated on the current setup.

DNX64 is proprietary vendor software. Review the files under `vendor/dnx64/` and redistribution terms before publishing a public binary/package containing those DLLs.

## Release limitations

Before describing this as a finished general-purpose hardware platform, keep these boundaries explicit:

- automatic driver detection is not production-authoritative;
- Valves are not implemented;
- Analytics currently analyzes recorded video, not single photos;
- software capability ranges are not automatically equivalent to complete real-hardware validation;
- lost serial communication can leave physical output state unknown;
- public packaging/installer and third-party redistribution review remain future work.

## For the next developer

A good order for onboarding is:

1. read this README;
2. read [Documentation Index](docs/INDEX.md);
3. read [Developer Guide](docs/DEVELOPER_GUIDE.md);
4. read [Software Architecture](docs/SOFTWARE_ARCHITECTURE.md) and [Multiboard Communication](docs/BOARD_COMMUNICATION.md);
5. read the page + engine document for the subsystem you are changing;
6. run the relevant tests before changing behavior;
7. use a hardware diagnostic tool before editing UI code when the problem may originate at the physical boundary.

The goal of this documentation structure is to keep overview material readable while leaving detailed subsystem knowledge close to the code it explains.
