# FluidicStudio Documentation Index

This file is the navigation hub for the FluidicStudio handoff documentation.
The goal is that a new developer can learn the architecture and hardware rules
from Markdown before opening large PyQt or backend modules.

## Recommended onboarding order

1. [README](../README.md) — project status and quick start.
2. [User documentation](user/INDEX.md) — setup, safe operation and page guides.
3. [Developer documentation](developer/INDEX.md) — coding rules, architecture and validation.
4. [User Guide](USER_GUIDE.md) — how the application is operated.
5. [Developer Guide](DEVELOPER_GUIDE.md) — coding rules and sources of truth.
6. [Software Architecture](SOFTWARE_ARCHITECTURE.md) — how the runtime is assembled.
7. [Board Communication](BOARD_COMMUNICATION.md) — how commands reach the Multiboard and how replies return.
8. [Hardware Architecture](HARDWARE_ARCHITECTURE.md) — physical driver/channel topology.
9. Read the specialist engine/page document for the feature you will change.

## Architecture and communication

| Document | Read it when you need to understand… |
| --- | --- |
| [Software Architecture](SOFTWARE_ARCHITECTURE.md) | application startup, board registry, shared services, page/Workspace synchronization, threads and state ownership |
| [Board Communication](BOARD_COMMUNICATION.md) | UART framing, command ACKs, pump transactions, firmware parsing, sensor stream initialization and shutdown |
| [Hardware Architecture](HARDWARE_ARCHITECTURE.md) | Multiboard driver groups, CH1–CH6 relationships and physical resources |
| [Hardware Limitations](HARDWARE_LIMITATIONS.md) | software limits, bench evidence, unverified areas and safety boundaries |
| [Pump + Wave Engine](PUMP_WAVE_ENGINE.md) | channel ownership, manual pumps, wave execution, shared frequency/carrier behavior and timing |
| [Sensor Pipeline](SENSOR_PIPELINE.md) | serial measurement path, units, integration, chart data and CSV logging |
| [Camera Engine](CAMERA_ENGINE.md) | OpenCV/DirectShow runtime, workers, shared preview and camera controls |
| [DNX64 Reference](DNX64_REFERENCE.md) | Dino-Lite DNX64 API functions, feature requirements and integration status |
| [Analytics Engine](ANALYTICS_ENGINE.md) | offline video detector/tracker/event/calibration/export pipeline |
| [Data and Sessions](DATA_SESSIONS.md) | runtime directories, JSON files, sessions, camera profiles and safe restore behavior |
| [UI Design System](UI_DESIGN_SYSTEM.md) | QSS/design tokens, state styling, compact Workspace rules and interaction consistency |

## Hardware research and diagnostics

| Document | Purpose |
| --- | --- |
| [Driver Detection](DRIVER_DETECTION.md) | safe fingerprinting and future automatic driver detection |
| [Testing and Diagnostics](TESTING_DIAGNOSTICS.md) | test groups, hardware probes, troubleshooting order and safe diagnostic rules |
| [Future Roadmap](FUTURE_ROADMAP.md) | known unfinished work and proposed future features |
| [Bartels Pump Control Research](BARTELS_PUMP_CONTROL_RESEARCH.md) | protocol research notes and source-finding queries |
| [Driver Detection README](DRIVER_DETECTION_README.md) | quick usage notes for the driver detection probe |
| [Driver Probe Test README](DRIVER_PROBE_TEST_README.md) | quick usage notes for the driver probe test |
| [Pump Test README](PUMP_TEST_README.md) | quick usage notes for the manual pump test script |
| [Firmware and Pump UI Changes](FIRMWARE_AND_PUMP_UI_CHANGES.md) | change log for firmware/pump UI work |
| [UI Redesign Plan](UI_REDESIGN_PLAN.md) | planned Tauri 2 + React migration of the PyQt6 UI |

## Page documentation

Each page document describes the user-facing behavior, the backend it relies on,
and the important UX rules that should not be accidentally changed. Known
implementation limitations are called out instead of being hidden behind an
“implemented” label.

- [Home](pages/HOME.md)
- [Pumps](pages/PUMPS.md)
- [Sensors](pages/SENSORS.md)
- [Wave](pages/WAVE.md)
- [Camera](pages/CAMERA.md)
- [Analytics](pages/ANALYTICS.md)
- [Workspace](pages/WORKSPACE.md)
- [Valves](pages/VALVES.md)

## If you are changing a specific thing

### “I need to add/change a Multiboard command”

Read:

1. [Board Communication](BOARD_COMMUNICATION.md)
2. [Hardware Architecture](HARDWARE_ARCHITECTURE.md)
3. `backend/protocol.py`
4. `backend/serial_manager.py`

Do not put raw command strings directly into UI pages unless the command is a
strictly local diagnostic. Production command syntax belongs in
`backend/protocol.py`.

### “I need to change Pumps or Waves”

Read:

1. [Pump + Wave Engine](PUMP_WAVE_ENGINE.md)
2. [Pumps page](pages/PUMPS.md) or [Wave page](pages/WAVE.md)
3. [Hardware Architecture](HARDWARE_ARCHITECTURE.md)

The critical distinction is:

```text
same driver != same channel
```

CH1–CH4 share driver-level settings but retain independent amplitude, ON/OFF and
channel ownership.

### “I need to add another sensor”

Read:

1. [Sensor Pipeline](SENSOR_PIPELINE.md)
2. [Board Communication](BOARD_COMMUNICATION.md)
3. [Sensors page](pages/SENSORS.md)
4. `backend/protocol.py`

A new sensor requires more than a new checkbox: command definitions, parser
format, unit policy, availability detection, data history and logging semantics
must all agree.

### “I need to change the camera”

Read:

1. [Camera Engine](CAMERA_ENGINE.md)
2. [DNX64 Reference](DNX64_REFERENCE.md)
3. [Camera page](pages/CAMERA.md)

Keep one OpenCV/DNX64 runtime shared by the full Camera page and Workspace.

### “I need to expose another Dino-Lite feature”

Read [DNX64 Reference](DNX64_REFERENCE.md) first. It marks each function as one
of:

- production-used;
- present but not exposed;
- hardware-feature dependent;
- vendor-wrapper only/unvalidated.

Do not expose a button merely because a DLL export exists.

### “I need to change Analytics”

Read:

1. [Analytics Engine](ANALYTICS_ENGINE.md)
2. [Analytics page](pages/ANALYTICS.md)

The UI is not the detector. Detector/tracker/event semantics belong below
`backend/analytics/`.

### “I need to add another Workspace panel”

Read:

1. [Workspace page](pages/WORKSPACE.md)
2. [Software Architecture](SOFTWARE_ARCHITECTURE.md)

Workspace panels are compact views over existing state. Avoid creating a second
backend/runtime for the same hardware.

### “I need to change where files are saved”

Read [Data and Sessions](DATA_SESSIONS.md) and change
`backend/application_paths.py` rather than scattering new paths through UI code.

### “Hardware is behaving strangely”

Read [Testing and Diagnostics](TESTING_DIAGNOSTICS.md) before editing UI code.
Start at the physical/transport boundary and work upward.

## Evidence levels used in these docs

The documentation deliberately distinguishes these statements:

- **Software-tested** — covered by committed automated tests.
- **Bench-validated** — exercised on named physical hardware with a retained result.
- **Vendor-described** — present in Dino-Lite/Bartels vendor API/material, but not necessarily verified here.
- **Planned** — future work, not current product behavior.

This distinction matters in a laboratory control application. A range shown by a
vendor API or a function exposed by a DLL is not automatically equivalent to a
feature verified on the exact connected device.
