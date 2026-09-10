# Sensors Page

The Sensors page displays live measurements from connected Multiboards, maintains graph history, integrates liquid-flow volume, and writes experiment logs.


For the complete raw serial → parser → history/logging path, see [Sensor Pipeline](../SENSOR_PIPELINE.md) and [Multiboard Communication](../BOARD_COMMUNICATION.md).

## User-facing functions

The page supports:

- board/sensor discovery and selection;
- current sensor values;
- live graphing;
- comparison of selected signals;
- Pause / Resume graph view;
- Fit Data;
- Live View;
- sample-rate selection;
- CSV logging;
- accumulated liquid volume.

## Measurement definitions

The current UI knows display definitions for:

```text
Liquid Flow Rate   µL/min
Pressure           mbar
Gas Flow Rate      mL/min
Analog 1–3         V
```

Actual availability depends on what the connected board reports. UI metadata is not proof that a physical sensor exists.

## Liquid-flow unit boundary

Firmware liquid-flow values are parsed as mL/min and converted exactly once in `backend/protocol.py`:

```text
firmware mL/min × 1000 → FluidicStudio µL/min
```

After that conversion, the value must remain in µL/min throughout the normal app/CSV path. A second conversion is a bug.

The raw firmware value is retained where useful for raw logging/debugging.

## Graph behavior

### Pause

Pause freezes the plotted view only. It does not stop serial acquisition and does not stop CSV logging.

### Fit Data

Fit Data adjusts the viewport to retained history.

### Live View

Live View returns to following the latest samples with automatic scaling.

The normal live graph keeps a bounded history window so an indefinitely running experiment does not grow UI memory without limit. Paused/manual-view behavior is handled carefully so the graph does not trim away the data the user is currently inspecting.

## Volume integration

Liquid flow is integrated using sample timestamps and the normalized µL/min values. The backend/page uses time-aware integration rather than assuming a perfectly fixed sample interval.

Large gaps are treated conservatively instead of assuming flow continued unchanged across a long missing-data interval.

## Logging

The normal CSV is intended for human use. Logging runs independently from the visible chart history.

A raw sidecar (`*_raw.csv`) preserves lower-level sample information for debugging/traceability.

Generated sensor logs belong under:

```text
user_data/sensor_logs/
```

## Workspace relationship

Sensor Workspace is a compact synchronized view. It shares:

- selected sensor;
- sample-rate/logging state;
- Pause/Fit/Live chart mode;
- live sensor data through the shared data path.

Workspace must not consume serial events in a way that starves the full Sensors page. `backend/sensor_data_hub.py` exists to fan out data without creating competing consumers.

## Relevant files

```text
ui/pages/Sensors.py
backend/protocol.py
backend/serial_manager.py
backend/sensor_data_hub.py
backend/csv_logger.py
backend/sensor_stream_capture.py
backend/sensor_stream_capture_live.py
```

## Diagnostics

Useful tools:

```text
tools/sensor_stream_probe.py
tools/sensor_stream_probe_live.py
tools/serial_capture.py
```

Start at raw serial when values or stream lifecycle are suspicious.

## Relevant tests

```text
test_sensor_data_hub.py
test_sensor_history.py
test_sensor_pause.py
test_sensor_stream_capture.py
test_csv_logger.py
test_ui_sensor_integration.py
```

## Navigation

```text
Shift+3
```
