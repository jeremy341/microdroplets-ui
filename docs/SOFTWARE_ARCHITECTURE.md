# Software Architecture

This document explains how FluidicStudio is assembled at runtime. It is intended
to answer “where does this state actually live?” before a developer has to trace
signals through `app.py` and multiple PyQt pages.

## 1. High-level architecture

```text
                         FluidicStudio
                              │
                  ┌───────────┴───────────┐
                  │                       │
                PyQt UI                backend
                  │                       │
        full pages + Workspace      hardware/services
                  │                       │
                  └──────── shared state ─┘
                              │
          ┌───────────────────┼────────────────────┐
          │                   │                    │
   Multiboard serial      Dino-Lite camera     local files
```

The most important design rule is that **physical state is not owned by a page**.
Pages display and request changes to shared backend objects.

## 2. Application shell: `app.py`

`app.py` owns application-wide responsibilities:

- creates the main window/navigation;
- creates full pages and the Workspace page;
- discovers/connects/disconnects Multiboards;
- creates one backend service bundle per connected board;
- selects the active board;
- coordinates session save/load;
- updates pages when board selection changes;
- coordinates safe shutdown.

`app.py` is deliberately an orchestration layer. Low-level command syntax should
not migrate into it.

## 3. One service bundle per physical Multiboard

When a COM port is connected, `connect_board()` creates:

```text
MultiboardConnection
        │
ChannelOwnershipManager
        │
PumpControlService
        │
WaveExecutionService
```

Those objects are stored in one board dictionary together with UI-facing driver
configuration and sensor status.

Simplified runtime board structure:

```python
board = {
    "name": "MB1",
    "port": "COM3",
    "firmware": "...",
    "connection": MultiboardConnection(...),
    "channel_ownership": ChannelOwnershipManager(),
    "pump_control": PumpControlService(...),
    "wave_execution": WaveExecutionService(...),
    "drivers": [...],
    "sensors": [...],
    "hardware_inventory": {...},
}
```

Do not treat this exact Python dictionary shape as a public API. The important
part is the ownership relationship: each physical board gets its own connection,
ownership manager and control services.

## 4. Active board vs connected boards

FluidicStudio can retain multiple connected board records, but one `active_port`
selects which board the normal pages are currently controlling.

`update_pages()` supplies:

- `connected_boards` where a page needs the full list;
- `active_board` where a page controls one selected board.

When adding a new board-aware page, prefer the same `set_connected_boards()` /
`set_active_board()` pattern rather than reaching into global variables.

## 5. Full pages and Workspace

Workspace is not an independent hardware controller.

```text
                    shared backend/state
                     /       |       \
                  Pumps     Wave    Camera ...
                    \         |       /
                      Workspace panels
```

Examples:

- Pump Workspace uses the active board's `PumpControlService` and ownership.
- Wave Workspace mirrors the actual Wave page/runtime state.
- Camera Workspace displays the same camera runtime/latest frame.
- Sensor Workspace subscribes to the same measurement stream/history.

Workspace-only state is presentation state, such as:

- which two panel types are displayed;
- splitter position;
- which two pump channels are shown in compact slots.

It should not contain a second copy of physical channel state.

## 6. Core sources of truth

| Concern | Authoritative location |
| --- | --- |
| Multiboard command syntax and parser | `backend/protocol.py` |
| serial transport + ACK lifecycle | `backend/serial_manager.py` |
| configured driver type | `data/driver_config.json` + `backend/driver_config.py` |
| channel/driver capabilities | `backend/driver_capabilities.py` |
| driver frequency state | `backend/driver_frequency_state.py` |
| manual/wave channel ownership | `backend/channel_ownership.py` |
| pump operations | `backend/pump_control.py` |
| automatic Wave runtime | `backend/wave_execution.py` |
| Wave generation/validation | `backend/waveform_engine.py` |
| camera hardware abstraction | `backend/camera_service.py` |
| persistent camera profiles | `backend/camera_profiles.py` |
| sensor history fan-out | `backend/sensor_data_hub.py` |
| sessions | `backend/session_manager.py`, `backend/session_runtime.py` |
| Analytics | `backend/analytics/` |
| runtime paths | `backend/application_paths.py` |

## 7. Threading model

FluidicStudio must keep blocking hardware operations away from the Qt GUI thread.

Major asynchronous boundaries include:

### Multiboard reader thread

`MultiboardConnection` owns a UART reader thread. It:

- reads raw bytes;
- assembles newline-delimited lines;
- parses replies;
- satisfies ACK waiters;
- emits `BackendEvent` objects;
- emits sensor measurements.

Writes are serialized with locks so acknowledged transactions cannot interleave.

### Flow-sensor initializer/watchdog thread

After a board connects, `initialize_liquid_flow()` runs in a daemon thread. It
performs firmware synchronization, stream setup and sample-based presence
validation without freezing Qt.

### Wave runner threads

Automatic waveforms run through `WaveformRunner`/`WaveExecutionService`. The
Wave thread sends time-based amplitude updates while the pump-control service
owns start/stop and ownership safety.

### Camera workers

Camera discovery/open/read/record/save and property writes are delegated to
workers so DirectShow/DNX64 calls do not block the GUI.

### CSV logger worker

Sensor CSV writing is asynchronous. The UI submits data; disk I/O happens in the
logger worker.

## 8. Event/state synchronization

There are two broad synchronization patterns.

### Backend event fan-out

`MultiboardConnection` publishes events both to:

- a queue consumed by existing UI logic;
- non-consuming subscribers used by additional views such as Workspace.

Subscribers are observers only. Exceptions in a subscriber must not kill the
serial reader.

### Shared object/model reads

Pump/Wave/Camera views read state from the same backend service/page runtime.
Periodic UI refreshes must not overwrite a control while the user is actively
editing it. This is why Workspace pump sliders protect drag/edit state.

## 9. Hardware state vs UI state

A UI value is not always proof of physical state.

Examples:

- an OFF pump can have a staged amplitude value in RAM;
- session restore loads values into RAM but never turns pumps ON;
- a failed/unacknowledged OFF can leave hardware state unknown;
- driver frequency state tracks the last acknowledged value;
- a Wave definition is a configuration; a Wave runtime is a separate execution.

Avoid naming a variable `is_on` if it is really only “the switch is visually
checked.”

## 10. Board disconnect/shutdown safety

Normal board disconnect follows this order:

```text
stop Wave runners
→ attempt per-channel Wave OFF finalizers
→ MultiboardConnection.close()
→ require acknowledged global POFF
→ stop sensor stream
→ close serial port
→ force-clear ownership only after connection is confirmed closed
```

If `POFF` is not acknowledged, normal disconnect is blocked. The connection is
kept open so the user can retry rather than falsely reporting a safe state.

## 11. Sessions do not restore active outputs

`backend/session_runtime.py` intentionally writes:

```text
enabled = False
```

when building/restoring pump profiles.

A loaded session can restore:

- amplitude configuration;
- frequency configuration;
- carrier waveform configuration;
- names/settings;

but it must not automatically start laboratory hardware.

## 12. Adding a new subsystem

Before adding code, decide which layer owns each responsibility:

```text
vendor/protocol fact     → backend low-level module
hardware policy          → backend service/capabilities
persistent data          → backend storage/session module
UI presentation          → ui/pages
compact duplicate view   → Workspace, reading same backend
```

Do not put a safety rule only in a button handler. If a rule protects physical
hardware, it belongs in the backend boundary that performs the operation.

## 13. Files to read next

- [Board Communication](BOARD_COMMUNICATION.md)
- [Hardware Architecture](HARDWARE_ARCHITECTURE.md)
- [Pump + Wave Engine](PUMP_WAVE_ENGINE.md)
- [Sensor Pipeline](SENSOR_PIPELINE.md)
- [Camera Engine](CAMERA_ENGINE.md)
- [Analytics Engine](ANALYTICS_ENGINE.md)
