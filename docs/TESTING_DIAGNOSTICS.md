# Testing and Hardware Diagnostics

FluidicStudio controls physical hardware, so debugging should proceed from the
hardware/transport boundary upward instead of changing UI code first.

## 1. Normal software test command

Development dependencies:

```powershell
python -m pip install -r requirements-dev.txt
```

Run:

```powershell
pytest -q
```

The release suite was intentionally pruned from many historical V-number/layout
checks while keeping behavioral tests for hardware semantics, data handling and
Workspace synchronization.

There is currently no committed CI workflow or coverage threshold. The local
suite contains source-only tests plus optional PyQt/vendor/hardware tests; a
missing dependency can silently reduce the executed coverage. Keep those lanes
explicit in validation reports.

## 2. What deserves a regression test

Always add a test when a bug could silently alter:

- pump ON/OFF safety;
- channel ownership;
- shared-driver frequency/carrier behavior;
- units/calibration;
- recorded/logged experiment data;
- session safe restore;
- camera backend selection/control semantics;
- Analytics count/geometry semantics;
- Workspace ↔ full-page state synchronization.

Avoid tests that only freeze arbitrary spacing/QSS strings unless there is no
better behavior-level assertion.

## 3. Test groups

### Serial/protocol

```text
test_protocol.py
test_serial_manager.py
test_raw_serial_capture.py
test_sensor_stream_capture.py
```

### Pumps/drivers/ownership

```text
test_pump_control.py
test_channel_ownership.py
test_live_frequency.py
test_driver_configuration.py
test_driver_detection_probe.py
test_compare_driver_probes.py
```

### Waves

```text
test_waveform_engine.py
test_waveform_library.py
test_wave_frequency_control.py
```

### Sensors/logging

```text
test_sensor_data_hub.py
test_sensor_history.py
test_sensor_pause.py
test_csv_logger.py
test_ui_sensor_integration.py
```

### Camera

```text
test_camera_service.py
test_camera_profiles.py
test_fps_profiles.py
test_dnx64_camera_backend.py
test_dnx64_attach_recovery.py
test_camera_exposure_validated_range.py
test_camera_performance_architecture.py
test_camera_led_controls.py
```

### Analytics

```text
test_analytics_backend.py
test_analytics_postprocessing.py
test_analytics_event_geometry.py
```

### Workspace

```text
test_workspace_backend.py
test_workspace_camera.py
test_workspace_splitter.py
test_workspace_pumps_smooth_controls.py
test_workspace_pumps_ux.py
test_workspace_ux_parity.py
test_workspace_tab_sync.py
```

### Sessions/app paths

```text
test_session_manager.py
test_session_runtime.py
test_application_paths.py
test_navigation_restore.py
```

## 4. Hardware diagnostic philosophy

A diagnostic tool should be explicit about whether it is:

```text
passive/read-only
reversible but state-changing
dangerous/output-starting
```

Most shipped tools are designed to avoid starting pumps.

Never label a tool “read-only” if it sends `POFF` or another state-changing
command, even if that command is safe.

“Read-only” also means no camera property writes, no image/file output, and no
sensor stream/calibration commands. A tool that initializes or reconfigures
hardware is state-changing even when it does not start a pump.

## 5. `hardware_health_check.py`

Default mode is passive. It checks:

- runtime directories;
- dependencies;
- visible serial ports;
- DNX64 runtime presence;
- application paths.

With a specified port it can perform a read-only Multiboard handshake.

Use it as the first environment check on a new PC.

## 6. `driver_detection_probe.py`

Purpose: collect **read-only driver fingerprints** from known physical setups.

It can send:

- `V`;
- blank/settings query;
- optional `P1V?` ... `P6V?` queries.

It must never translate an opaque `Driver:` token into a driver name on its own.
See [Driver Detection](DRIVER_DETECTION.md).

## 7. `compare_driver_probes.py`

Offline only. It compares labelled JSON captures and reports repeated/conflicting
fingerprints.

This is the correct place to build evidence before changing production driver
selection.

## 8. `serial_capture.py`

Records a baseline TX/RX transcript.

Its sequence can include `POFF`, so it is **not strictly read-only** even though
it never starts a pump. Use the driver probe when a truly read-only fingerprint
is required.

## 9. Sensor probes

### `sensor_stream_probe.py`

Safe sensor-focused sequence:

```text
DFOFF → L0 → DFON → capture → DFOFF
```

No pump start commands.

### `sensor_stream_probe_live.py`

Adds live output and an initial board synchronization step.

Use these when measurement framing/presence is uncertain.

## 10. `dnx64_probe.py`

DNX64 attachment diagnostic. Treat it as state-changing until the initialization
path is isolated: the current implementation initializes the vendor backend with
controls enabled and may write current exposure values.

Checks:

- Windows/platform architecture;
- DLL candidate paths;
- whether DNX64 loads;
- device enumeration/identity/control attachment state.

Do not run it during an experiment. Verify the exact implementation before
calling it passive.

## 11. `camera_backend_smoke_test.py`

Exercises the production camera backend and can verify that a camera opens and
produces frames/capabilities.

It also writes a PNG as part of the smoke test, so it is not read-only. Keep its
output in a disposable diagnostics location and use a safe camera state.

Use it before debugging Camera.py if the preview/control problem might be below
Qt.

## 12. Exposure diagnostics

```text
exposure_diagnostic.py
exposure_resolution_diagnostic.py
```

These are hardware-changing diagnostics. They intentionally exercise exposure
across selected percentages/resolutions and record measurements.

Do not run them during an experiment.

The sensor stream probes are also reversible state-changing tools: they send
`DFOFF`, calibration and `DFON` commands before stopping the stream. They do not
start pumps, but they can affect an active sensor workflow.

## 13. Recommended troubleshooting order

### Multiboard not connecting

1. Windows Device Manager / COM visibility.
2. CP210x driver/cable/USB port.
3. `hardware_health_check.py`.
4. raw/read-only firmware probe.
5. `MultiboardConnection` logs/events.
6. only then inspect page logic.

### Pump command rejected

1. confirm selected driver configuration;
2. check amplitude/frequency capability range;
3. check channel ownership;
4. inspect raw command and ACK/error line;
5. reproduce with a minimal backend test before changing UI.

### Flow sensor missing

1. run sensor stream probe;
2. verify DFOFF/L0/DFON ACKs;
3. inspect raw measurement lines;
4. verify parser format;
5. verify sample-based availability detection.

### Camera preview missing

1. Windows camera availability;
2. `camera_backend_smoke_test.py`;
3. OpenCV/DirectShow access;
4. DNX64 only if controls/identity are the failing part.

### Camera preview works, controls fail

1. `dnx64_probe.py`;
2. check DLL path/version/dependencies;
3. check SDK index/device mapping;
4. check property readback;
5. do not repeatedly reopen the video handle from slider events.

### Workspace differs from full page

1. identify the real source of truth;
2. confirm both views reference the same active board/page/runtime;
3. check refresh timing/edit protection;
4. add a synchronization regression test.

## 14. Real-hardware test discipline

When changing hardware behavior:

- label the exact board/camera/sensor/driver setup;
- record firmware/runtime version;
- keep before/after diagnostic output;
- test one change at a time;
- do not infer physical state from UI state;
- confirm stop/OFF behavior before increasing complexity.

## 15. Test fixtures vs real recordings

Analytics real-video benchmarking is valuable but large recordings should not be
silently bundled into the source release. Keep a documented external benchmark
set or small approved fixtures.

## 16. CI limitations

Most backend logic can run in CI without hardware through mocks/fakes. Direct Qt
integration requires PyQt6 and an appropriate headless setup. Real hardware
validation remains a separate layer and should be recorded as such.

The current source-only test environment also exposes a packaging assumption:
`tests/test_application_paths.py` expects a bundled `vendor/dnx64/DNX64.dll`,
while the repository intentionally does not track proprietary vendor files.
Maintain separate source-only, installed-runtime, and connected-hardware jobs
until that fixture contract is corrected.
