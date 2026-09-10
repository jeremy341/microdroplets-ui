# Workspace Page

Workspace combines two compact subsystem views for experiments that need controls/data visible side by side.


For shared-state/runtime rules, see [Software Architecture](../SOFTWARE_ARCHITECTURE.md). Workspace panels must remain views over existing backends.

## Available panels

The current Workspace supports:

```text
Pumps
Sensors
Wave
Camera
```

It does not embed Analytics or Valves in the current release.

## Layout behavior

A selected pair starts at approximately 50/50. The center `QSplitter` is draggable afterward.

The splitter is a presentation choice only; it is not part of hardware/session state beyond Workspace layout preferences.

Workspace stays open when switching to another page and back.

## Core architecture rule

Workspace is **a second view of existing state**, never a second hardware backend.

```text
                 shared backend/runtime
                  ↙              ↘
             full page         Workspace
```

This rule is especially important for camera handles, pump ownership, Wave runtime, and sensor streams.

## Pump Workspace

Pump Workspace shows up to two user-selectable channels.

Each slot contains compact versions of the useful Pumps controls:

- channel selector;
- ON/OFF switch;
- driver frequency;
- signal mode where supported;
- amplitude.

No duplicate Start/Stop buttons are needed because the switch controls the pump.

If two displayed channels share a physical driver, frequency is shared while amplitude and ON/OFF remain independent.

The panel uses optimistic/pending UI state so acknowledged hardware writes do not cause sliders to snap backward while being dragged.

## Sensor Workspace

Sensor Workspace provides compact access to:

- sample rate;
- logging controls;
- sensor selection;
- live plot;
- Pause;
- Fit;
- Live.

It shares the same sensor data hub/state as the full Sensors page.

## Wave Workspace

Wave Workspace edits the same draft/runtime as the full Wave page.

It mirrors important runtime UX:

- Test disabled when selected channel is manually owned;
- “Pump in use” state;
- editor locked while Wave is running;
- Stop available for the running Wave;
- live driver frequency remains available according to backend policy.

## Camera Workspace

Camera Workspace reuses the full Camera page/runtime:

- same selected device;
- same open state;
- same resolution/FPS;
- same exposure/brightness/LED state;
- same recording state;
- same latest frame.

It never opens a second `VideoCapture`.

The compact preview uses center-cropped cover rendering to fill the available half-width panel without stretching the image. Only the UI preview is cropped.

## Synchronization contract

Changes must propagate both ways:

| Workspace | Full page |
| --- | --- |
| Pumps | Pumps |
| Sensors | Sensors |
| Wave | Wave |
| Camera | Camera |

Synchronization includes more than numeric values. It also includes connection state, running/ownership state, pending operations, locks, and relevant error state.

Workspace-specific state should be limited to presentation choices such as selected panel types and which two pump channels are displayed.

## Backend files

Workspace layout/model persistence is under:

```text
backend/workspace/models.py
backend/workspace/controller.py
backend/workspace/registry.py
backend/workspace/presets.py
backend/workspace/persistence.py
```

The UI panels are implemented in:

```text
ui/pages/Workspace.py
```

## Tests

Important coverage includes:

```text
test_workspace_backend.py
test_workspace_splitter.py
test_workspace_tab_sync.py
test_workspace_ux_parity.py
test_workspace_camera.py
test_workspace_pumps_ux.py
test_workspace_pumps_smooth_controls.py
```

When adding a Workspace feature, first ask:

> Which existing page/backend state is this a view of?

If the answer is “none”, creating new hardware state inside Workspace is probably the wrong architecture.
