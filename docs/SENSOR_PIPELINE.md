# Sensor Acquisition, History and Logging Pipeline

This document describes the full path from a Multiboard sensor reply to the
Sensors page, Workspace, CSV and integrated volume.

## 1. Current validated sensor path

The primary physical sensor developed and tested with FluidicStudio is the
Sensirion SLF3S-1300F liquid-flow path through the Multiboard.

Other sensor command definitions exist in `backend/protocol.py`, but they should
not be described as equally validated without hardware evidence.

## 2. End-to-end data flow

```text
Multiboard sensor
      ↓ serial bytes
MultiboardConnection reader thread
      ↓ complete line
protocol.parse_reply()
      ↓ ParsedMeasurement
SensorSample
      ↓ BackendEvent("measurement")
      ├─ UI/event consumers
      ├─ SensorDataHub history/latest
      ├─ Workspace sensor view
      └─ CSV logger/session logic
```

## 3. Presence detection

A sensor is not marked present merely because the UI knows a connector/sensor
name.

For liquid flow, presence is established after **two valid finite measurement
samples** arrive following the acknowledged setup sequence.

Zero flow is a valid sample. Negative flow is also a valid numeric sample; it
must not be interpreted as “sensor missing.”

## 4. Initialization sequence

The connection's automatic liquid-flow initializer performs:

```text
V
→ DFOFF
→ L0 (water calibration)
→ DFON
→ wait for 2 valid samples
```

This runs outside the Qt GUI thread.

If samples do not arrive, the connection retries rather than permanently showing
an available sensor based only on an ACK.

## 5. Watchdog behavior

After a valid stream starts, the initializer remains as a lightweight watchdog.
If the valid measurement count does not change for approximately five seconds,
it publishes a retry event and returns to the complete initialization sequence.

This is intentionally stronger than simply re-sending `DFON`: it re-synchronizes
the known calibration/stream state.

## 6. Parser and units

`backend/protocol.py` is the only unit-normalization boundary.

Liquid-flow firmware value:

```text
mL/min
```

FluidicStudio internal/display value:

```text
µL/min
```

Conversion:

```text
value_ui = value_raw_ml_min × 1000
```

`SensorSample` retains `raw_value_ml_min` for diagnostic/raw logging, while
`value` contains the normalized µL/min result.

Do not convert again in Sensors.py, Workspace or CSV code.

## 7. Accepted measurement formats

The parser supports:

```text
V=<number>
```

when the active sensor makes that line unambiguous, and marked lines such as:

```text
RSLF ...
RSDPC ...
CO2 ...
VOC ...
```

Unknown lines are preserved as unknown events; the parser does not scrape any
number-looking substring out of arbitrary firmware diagnostics.

## 8. Sensor sample structure

A `SensorSample` includes:

- UTC timestamp;
- elapsed seconds since board open;
- board port;
- sensor ID;
- normalized value;
- unit;
- accumulated volume where applicable;
- original raw line;
- raw liquid-flow mL/min where applicable.

This structure is the semantic boundary between serial parsing and higher-level
consumers.

## 9. Accumulated liquid volume

Volume integration is calculated in `MultiboardConnection`, not in the chart.

Method:

```text
trapezoidal integration
```

For consecutive flow samples `q0`, `q1` in µL/min separated by `dt` seconds:

```text
ΔV = ((q0 + q1) / 2) × dt / 60
```

The result is µL.

### Missing-data rule

If the receive gap is more than two seconds, no volume is invented across that
gap. A new integration segment starts with the next sample.

## 10. SensorDataHub

`backend/sensor_data_hub.py` provides a thread-safe short-term history shared by
multiple views.

It tracks by:

```text
(board_port, sensor_id)
```

and stores:

- recent history;
- latest value;
- whether the series has ever become available.

It subscribes to connection events without consuming the normal event queue.

## 11. Chart/display state vs measurement state

UI controls such as:

```text
Pause
Fit
Live
selected series
```

are presentation controls. They must not stop or transform the underlying
serial stream unless explicitly designed to do so.

The V76 Workspace synchronization work ensures compact and full sensor views
represent the same relevant page state.

## 12. Filtering policy

During hardware validation, raw values are authoritative.

Do not silently introduce:

- EMA;
- median filtering;
- deadbands;
- spike rejection;
- duplicate unit conversion.

If a future graph-only smoothing feature is added, it should be explicit and
must not change the raw CSV or integrated data unless that is a separately
specified processing pipeline.

## 13. CSV logging

`backend/csv_logger.py` contains `AsyncCsvLogger`.

Important principles:

- disk I/O occurs in a worker;
- the producer/UI submits structured samples;
- logger failure is tracked explicitly;
- stopping waits for the worker to finish/flush;
- filenames live under the application runtime path system.

Generated logs normally belong under:

```text
user_data/sensor_logs/
```

See [Data and Sessions](DATA_SESSIONS.md).

## 14. Raw diagnostic capture vs normal logging

These are different purposes.

### Normal CSV

Human-oriented experiment data based on parsed/normalized samples.

### Raw serial capture

Preserves TX/RX bytes/text to diagnose firmware or framing behavior before
parsing assumptions are applied.

Use the raw tools when the problem might be below the parser.

## 15. Safe sensor diagnostics

### `tools/sensor_stream_probe.py`

Runs the controlled sequence:

```text
DFOFF → L0 → DFON → capture → DFOFF → post-stop observation
```

No pump is started.

### `tools/sensor_stream_probe_live.py`

Same concept with live terminal output and an initial `V` synchronization step.

These tools are preferred over editing the normal UI when investigating a new
firmware format.

## 16. Adding a new sensor

A complete integration should answer all of these:

1. What are the start and stop commands?
2. Does it require calibration/setup commands?
3. What exact reply markers/formats are observed?
4. What raw unit does firmware send?
5. What canonical unit does FluidicStudio expose?
6. How is physical presence established?
7. Can multiple streams run simultaneously on the firmware?
8. Does the new stream interfere with current liquid-flow streaming?
9. How should it appear in SensorDataHub?
10. How should it be logged?
11. What tests/diagnostic capture prove the behavior?

Do not add a sensor card before these questions are answered.

## 17. Relevant files

```text
backend/protocol.py
backend/serial_manager.py
backend/sensor_data_hub.py
backend/csv_logger.py
backend/sensor_stream_capture.py
backend/sensor_stream_capture_live.py
ui/pages/Sensors.py
ui/pages/Workspace.py
```

## 18. Relevant tests

```text
test_protocol.py
test_serial_manager.py
test_sensor_data_hub.py
test_sensor_history.py
test_sensor_pause.py
test_sensor_stream_capture.py
test_csv_logger.py
test_ui_sensor_integration.py
```
