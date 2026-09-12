# Valves Page

The Valves page is intentionally a placeholder in the current release.

This is a user-visible placeholder only; no valve command, inventory or safe
hardware state is currently implemented.


For planned implementation boundaries, see [Future Roadmap](../FUTURE_ROADMAP.md) and [Hardware Limitations](../HARDWARE_LIMITATIONS.md).

## Current status

```text
Valve control: NOT IMPLEMENTED
```

The page exists so the product structure has a reserved location for future valve hardware without pretending that a backend or safe control model already exists.

## What not to do

Do not make the page look functional by adding buttons that only update local UI state.

A real implementation must start from the hardware boundary:

1. identify the actual valve driver/hardware;
2. document command protocol and electrical/safety limits;
3. implement backend capability/configuration models;
4. implement acknowledged state changes where appropriate;
5. decide ownership/interlock behavior with pumps/waves;
6. add diagnostics and tests;
7. only then expose controls in the page.

## Developer entry point

Current placeholder:

```text
ui/pages/Valves.py
```

The implementation should follow the same principle as the other subsystems: hardware truth in the backend, UI as a view/controller of that state.

## Navigation

```text
Shift+7
```
