# FluidicStudio Developer Guide

This is the handoff document for developers continuing FluidicStudio. Detailed subsystem behavior is intentionally split into specialist documents and per-page guides instead of being repeated here.

## 1. First principle: one backend state, multiple views

The application now contains full pages and compact Workspace panels. They must remain views of the **same backend/runtime state**.

```text
                  backend / runtime truth
                 ↙                      ↘
             full page               Workspace
```

Do not create `WorkspacePumpState`, a second camera handle, or a second Wave runtime when the existing backend already owns the physical state.

Workspace-specific state should be presentation-only: panel selection, compact channel selection, splitter/layout preferences.

## 2. Repository map

```text
app.py
    application shell, navigation, board lifecycle, page coordination,
    session orchestration and shutdown ordering

backend/
    serial protocol/connection, pump ownership/control, waves, sensors,
    camera services, sessions, paths and configuration

backend/analytics/
    offline droplet analysis pipeline

backend/workspace/
    Workspace model, presets, registry and persistence

ui/pages/
    full PyQt pages plus compact Workspace panel implementations

style.qss
    application styling

data/
    version-controlled driver configuration and saved waveforms

tools/
    safe diagnostics/probes

tests/
    curated regression/unit/integration tests

user_data/
    generated runtime data; ignored by Git

vendor/dnx64/
    optional vendor runtime/notices for Dino-Lite controls
```

## 3. Specialist documentation

Start with the task-oriented [Documentation Index](INDEX.md). The specialist documents are intentionally detailed enough that normal architecture work should not require reverse-engineering large UI files first.

### Architecture / hardware / engines

- [Software Architecture](SOFTWARE_ARCHITECTURE.md)
- [Multiboard Communication](BOARD_COMMUNICATION.md)
- [Hardware and Board Architecture](HARDWARE_ARCHITECTURE.md)
- [Hardware Limitations](HARDWARE_LIMITATIONS.md)
- [Pump + Wave Engine](PUMP_WAVE_ENGINE.md)
- [Sensor Pipeline](SENSOR_PIPELINE.md)
- [Camera Engine](CAMERA_ENGINE.md)
- [DNX64 API Reference](DNX64_REFERENCE.md)
- [Analytics Engine](ANALYTICS_ENGINE.md)
- [Data and Sessions](DATA_SESSIONS.md)
- [UI Design System](UI_DESIGN_SYSTEM.md)

### Development / roadmap

- [Testing and Diagnostics](TESTING_DIAGNOSTICS.md)
- [Future Driver Detection](DRIVER_DETECTION.md)
- [Future Roadmap](FUTURE_ROADMAP.md)

### UI pages

- [Home](pages/HOME.md)
- [Pumps](pages/PUMPS.md)
- [Sensors](pages/SENSORS.md)
- [Wave](pages/WAVE.md)
- [Camera](pages/CAMERA.md)
- [Analytics](pages/ANALYTICS.md)
- [Workspace](pages/WORKSPACE.md)
- [Valves](pages/VALVES.md)

Read the page document before significantly changing that page.

## 4. Sources of truth

| Concern | Source of truth |
| --- | --- |
| physical configured pump drivers | `data/driver_config.json` + `backend/driver_config.py` |
| driver channel groups/ranges | `backend/driver_capabilities.py` |
| raw command syntax/reply parsing | `backend/protocol.py` |
| serial connection/streaming | `backend/serial_manager.py` |
| channel ownership | `backend/channel_ownership.py` |
| pump hardware operations | `backend/pump_control.py` |
| acknowledged driver frequency | `backend/driver_frequency_state.py` |
| saved wave definitions | `backend/waveform_library.py` / `data/waveforms.json` |
| generated wave steps | `backend/waveform_engine.py` |
| Wave runtime | `backend/wave_execution.py` |
| camera runtime | `backend/camera_service.py` + one Camera page runtime |
| sensor sample fan-out | `backend/sensor_data_hub.py` |
| session/path policy | `backend/session_manager.py`, `backend/application_paths.py` |
| Analytics semantics | `backend/analytics/*` |
| Workspace layout model | `backend/workspace/*` |

Keep rules at the correct boundary instead of duplicating them in button callbacks.

## 5. Pump architecture rules that must not regress

The physical driver groups are:

```text
F0 / Driver 0 → CH1–CH4
F1 / Driver 1 → CH5
F2 / Driver 2 → CH6
```

For CH1–CH4:

```text
shared:       frequency, supported carrier mode
per-channel:  amplitude, ON/OFF, ownership
```

**Same driver does not mean one active channel.** CH1 manual + CH2 manual is valid; CH1 manual + CH2 Wave is valid. Only competing control of the same physical channel is blocked by ownership.

Frequency is live/shared. Carrier mode is changed only when its full driver group is idle.

The pump backend uses acknowledgement and rollback logic. On ambiguous transport failure, do not casually clear ownership or display a false OFF state.

## 6. UI polling and asynchronous hardware

Qt must stay responsive while serial/camera/file operations run.

Important patterns already established:

- do not overwrite a slider while `isSliderDown()`;
- avoid overwriting actively edited spinboxes;
- keep optimistic pending values while an acknowledged command is in flight;
- revert only on failure;
- send blocking/slow hardware work outside the GUI thread where required.

The historical Workspace frequency “snap back then jump to cursor” bug came from polling acknowledged backend state over an actively dragged control. Preserve the current interaction guards.

## 7. Sensor data semantics

Liquid flow is normalized once in `backend/protocol.py`:

```text
firmware mL/min → application µL/min
```

Do not convert again in graphing or CSV.

Logging and visible graph history are independent. Pausing a chart is a view operation, not an acquisition stop.

`SensorDataHub` allows multiple views to observe data without competing to consume serial events.

## 8. Camera architecture

The full Camera page and Camera Workspace must share one runtime/video handle. OpenCV/DirectShow owns frames; DNX64 adds Dino-Lite controls where available.

Do not reintroduce LED intensity simply because low-level FLC methods exist. The product UI intentionally exposes LED ON/OFF only.

Read [Camera Engine](CAMERA_ENGINE.md) before changing workers, startup, exposure, modes, or Workspace preview.

## 9. Analytics architecture

Analytics is currently a video pipeline:

```text
VideoSource
→ detection/refinement
→ tracking/stitching
→ temporal crossing association
→ geometry selection/repair
→ velocity/spacing/generation metrics
→ AnalysisResult/cache/CSV
```

A counted event and valid geometry are intentionally distinct.

Continue detector work through benchmarks on multiple real recordings and error classification, not blind threshold tuning on one video.

A future still-image path should be geometry-only unless an external time reference exists.

## 10. Workspace contract

Workspace currently supports Pumps, Sensors, Wave and Camera.

Every synchronized panel must reflect changes both ways, including:

- values;
- running state;
- ownership/locks;
- connection state;
- pending operations;
- relevant errors.

The splitter/layout remains presentation state.

See [Workspace Page](pages/WORKSPACE.md) for current compact-panel semantics.

## 11. Sessions and runtime files

Version-controlled state:

```text
data/driver_config.json
data/waveforms.json
```

Generated state:

```text
user_data/captures/
user_data/sensor_logs/
user_data/sessions/
user_data/diagnostics/
user_data/exports/
user_data/analytics/
user_data/camera_profiles.json
user_data/analytics_settings.json
user_data/workspace_settings.json
```

`backend/application_paths.py` is responsible for runtime paths/migration.

Session restore must remain conservative: restore configuration/UI state without silently restarting old physical pump/wave outputs.

## 12. Diagnostics strategy

Use the smallest probe that reaches the failing physical boundary.

| Question | Tool |
| --- | --- |
| board/COM basic health | `tools/hardware_health_check.py` |
| raw serial transcript | `tools/serial_capture.py` |
| sensor stream | `tools/sensor_stream_probe.py` |
| live sensor transcript | `tools/sensor_stream_probe_live.py` |
| driver fingerprint | `tools/driver_detection_probe.py` |
| compare driver probes | `tools/compare_driver_probes.py` |
| DNX64 attachment | `tools/dnx64_probe.py` |
| camera backend | `tools/camera_backend_smoke_test.py` |
| exposure behavior | `tools/exposure_diagnostic.py` / `tools/exposure_resolution_diagnostic.py` |

Debug from hardware inward. If raw serial is wrong, do not start by changing the graph.

## 13. Tests

The release test suite has already been pruned to remove many obsolete V-number/layout/string tests while keeping behavioral coverage.

Run:

```powershell
pytest -q
```

Important groups:

```text
test_protocol.py
test_serial_manager.py
test_pump*.py
test_channel_ownership.py
test_live_frequency.py
test_wave*.py
test_sensor*.py
test_camera*.py
test_analytics*.py
test_workspace*.py
test_session*.py
test_driver*.py
```

Prefer behavioral tests. Avoid freezing arbitrary QSS/source strings unless the architecture itself cannot be tested more directly.

A bug that can silently change physical state, units, ownership, or recorded experiment data deserves a regression test.

## 14. Comments and code style

The release intentionally avoids over-commenting.

Good comments explain **why**:

```python
# Keep ownership after an unconfirmed OFF because the physical output state
# is unknown and another controller must not take over the channel.
```

Poor comments narrate obvious Python:

```python
# Set x to 5
x = 5
```

When code needs a long explanation, prefer placing the architectural explanation in the relevant Markdown document and leaving a short pointer/reason in code.

## 15. Known roadmap

See [Future Roadmap](FUTURE_ROADMAP.md) for the maintained handoff backlog. Major unfinished areas include verified automatic driver detection, Valves, still-image Analytics, broader real-video benchmarking, hardware validation coverage, packaging and vendor-runtime distribution review.

## 16. External references

Useful manufacturer/project references:

- Bartels Electronic Driver Datasheet: `https://bartels-mikrotechnik.de/wp-content/uploads/2025/03/datasheet-electronic-driver.pdf`
- Bartels mp-Multiboard2: `https://bartels-mikrotechnik.de/product/mp-multiboard2-en/`
- Bartels Software Manual: `https://bartels-mikrotechnik.de/wp-content/uploads/2025/03/software-manual.pdf`
- Bartels BP7 pump: `https://bartels-mikrotechnik.de/product/the-bartels-pump-bp7-piezo-pump/`
- Sensirion SLF3S-1300F: `https://sensirion.com/products/catalog/SLF3S-1300F`
- Dino-Lite AM4113T: `https://dino-lite.eu/en/products/microscopes/universal/am4113t`
- Dino-Lite DNX64 Python API: `https://github.com/dino-lite/DNX64-Python-API`

Treat manufacturer specification, software policy, and bench validation as different levels of evidence.
