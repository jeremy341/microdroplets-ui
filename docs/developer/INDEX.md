# FluidicStudio developer documentation

This is the engineering entry point. Existing root-level technical documents
remain canonical to preserve stable links; this index separates developer
material from the user route without duplicating every document.

## Required orientation

1. [Developer Guide](../DEVELOPER_GUIDE.md)
2. [Software Architecture](../SOFTWARE_ARCHITECTURE.md)
3. [Board Communication](../BOARD_COMMUNICATION.md)
4. [Hardware Architecture](../HARDWARE_ARCHITECTURE.md)
5. [Testing and Diagnostics](../TESTING_DIAGNOSTICS.md)
6. [Hardware validation matrix](HARDWARE_VALIDATION_MATRIX.md)

## Runtime and persistence

- [Pump + Wave Engine](../PUMP_WAVE_ENGINE.md)
- [Sensor Pipeline](../SENSOR_PIPELINE.md)
- [Camera Engine](../CAMERA_ENGINE.md)
- [Analytics Engine](../ANALYTICS_ENGINE.md)
- [Data and Sessions](../DATA_SESSIONS.md)

## Hardware and vendor boundary

- [Hardware Limitations](../HARDWARE_LIMITATIONS.md)
- [DNX64 Reference](../DNX64_REFERENCE.md)
- [Driver Detection](../DRIVER_DETECTION.md)

## UI and future work

- [UI Design System](../UI_DESIGN_SYSTEM.md)
- [Future Roadmap](../FUTURE_ROADMAP.md)
- [Page documents](../INDEX.md#page-documentation)

## Engineering evidence vocabulary

Every claim should identify its evidence level:

- **Software-tested** — covered by committed automated tests.
- **Bench-validated** — exercised on named physical hardware with a retained
  result.
- **Vendor-described** — derived from a vendor API, manual, or DLL export.
- **Planned** — intended future behavior, not current behavior.

Do not use “validated” as a blanket synonym for “implemented” or
“vendor-described”.
