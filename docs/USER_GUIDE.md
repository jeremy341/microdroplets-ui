# FluidicStudio User Guide

This guide explains how to install, connect, operate, and safely shut down FluidicStudio. Detailed control descriptions are split into one document per page so this guide stays readable. For setup-first navigation, use [user documentation](user/INDEX.md).

## 1. What FluidicStudio is

FluidicStudio is a Windows/PyQt6 application for a microfluidic setup containing:

- Bartels mp-Multiboard2 pump control;
- live sensor acquisition and CSV logging;
- generated pump waveforms;
- Dino-Lite camera preview/capture/recording;
- offline droplet video Analytics;
- a two-panel Workspace;
- session save/restore.

The Valves page is reserved for future hardware and is not functional in this release.

## 2. Install and run

Recommended environment: Windows 10/11 with Python 3.11–3.13.

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python app.py
```

For development/testing:

```powershell
python -m pip install -r requirements-dev.txt
pytest -q
```

The test suite is environment-sensitive. PyQt, camera, vendor and hardware
tests may be skipped or require a device; a passing source-only run is not a
bench validation.

## 3. Connect the Multiboard

Use the application connection control to choose the correct COM port.

The expected board protocol is:

```text
115200 baud, 8N1, CRLF-terminated ASCII
```

If the board does not connect, first check power, USB cable, COM port, and whether another program already has the serial port open.

## 4. Verify pump-driver configuration

Before enabling pumps, confirm that the physical driver modules match:

```text
data/driver_config.json
```

The shipped configuration expects:

```text
CH1–CH4  mp-Highdriver4
CH5      mp-Lowdriver
CH6      mp-Driver
```

CH5 can be configured for the supported alternative `highdriver` model when that is what is physically installed.

Changing the JSON does not change hardware. Power the Multiboard down before inserting/removing drivers, update configuration to match the real hardware, and restart FluidicStudio.

Read [Hardware and Board Architecture](HARDWARE_ARCHITECTURE.md) and [Hardware Limitations](HARDWARE_LIMITATIONS.md) before changing physical driver configuration.

## 5. Page guide

Use the dedicated page documentation for exact behavior:

- [Home](pages/HOME.md) — board/status overview.
- [Pumps](pages/PUMPS.md) — manual pump control, shared driver frequency, independent channel amplitude/ON-OFF.
- [Sensors](pages/SENSORS.md) — live measurements, graphing, volume and CSV logging.
- [Wave](pages/WAVE.md) — waveform generation, saved definitions and execution.
- [Camera](pages/CAMERA.md) — preview, capture, recording and Dino-Lite controls.
- [Analytics](pages/ANALYTICS.md) — offline droplet analysis for recorded video.
- [Workspace](pages/WORKSPACE.md) — synchronized two-panel compact experiment view.
- [Valves](pages/VALVES.md) — current placeholder/future subsystem.

## 6. Important pump concept

Channels on one physical driver can still run independently.

For CH1–CH4:

```text
shared:       driver frequency, supported carrier mode
independent:  amplitude, ON/OFF, manual/wave ownership
```

So CH1 and CH2 may both be ON with different amplitudes. If F0 changes, the frequency changes for all CH1–CH4 channels because F0 belongs to the driver.

A manual pump and a generated Wave cannot control the **same channel** at the same time. A sibling channel may still be used independently.

## 7. Recommended experiment workflow

1. Verify tubing, electrical connections, driver modules, and sensor/camera placement.
2. Start FluidicStudio.
3. Connect the correct Multiboard COM port.
4. Confirm the configured driver model/ranges match the physical board.
5. Check sensor direction/units before relying on logged values.
6. Start the camera if visual recording is required.
7. Configure Pumps or Wave before activating the output.
8. Start sensor logging and/or video recording.
9. Start the intended manual pump or waveform.
10. Observe the experiment through the appropriate page or Workspace.
11. Stop pumps/waves before changing physical hardware/tubing where required.
12. Stop logging/recording cleanly.
13. Use Analytics on the saved video if needed.

## 8. Workspace usage

Workspace can show two compact panels selected from Pumps, Sensors, Wave, and Camera.

The two views start around 50/50 and can be resized with the center splitter.

Workspace is not a second control system. Changes are synchronized with the full pages. For example, changing CH2 amplitude in Pump Workspace changes the same CH2 state shown on Pumps.

## 9. Sessions

Session Management saves/restores application configuration and UI state.

Save and load are intended for an idle application and are blocked while sensor
logging, camera recording, or pump/Wave outputs are active. A Workspace splitter
starts around 50/50 and is currently a runtime presentation choice rather than
a reliable persisted experiment setting.

Loading a session must **not** be interpreted as permission to automatically resume physical outputs. Pumps and waveforms should be explicitly restarted by the operator.

Generated sessions are stored under:

```text
user_data/sessions/
```

## 10. Generated files

Runtime files belong under `user_data/`:

```text
captures/       photos and videos
sensor_logs/    sensor CSV logs
sessions/       saved sessions
diagnostics/    hardware probe/transcript output
exports/        user-facing exports
analytics/      analysis results/cache
```

Additional settings files can include:

```text
camera_profiles.json
analytics_settings.json
workspace_settings.json
```

These are intentionally excluded from normal source control.

## 11. Keyboard navigation

```text
Shift+1  Home
Shift+2  Pumps
Shift+3  Sensors
Shift+4  Wave
Shift+5  Camera
Shift+6  Analytics
Shift+7  Valves

Ctrl+Tab        next page
Ctrl+Shift+Tab  previous page
```

Workspace is opened through its sidebar button/builder.

Some custom Sensors and Analytics interactions are mouse/drag-oriented and do
not currently have complete keyboard equivalents. This is a known UI limitation,
not a reason to infer that the underlying hardware operation is unavailable.

## 12. Troubleshooting quick reference

### CH1 and CH2 frequency move together

Expected. They share F0. Their amplitudes and ON/OFF remain independent.

### Wave says “Pump in use”

The selected physical channel is manually owned. Turn that same channel OFF before starting a Wave on it.

### Sensor values look 1000× too large/small

Check mL/min versus µL/min. FluidicStudio already converts normal liquid-flow readings to µL/min once at the protocol boundary.

### Camera preview works but brightness/exposure/LED does not

The OpenCV video path is working but the DNX64 control path may not be attached correctly. Read [Camera Engine](CAMERA_ENGINE.md) or run the provided camera diagnostics.

### Analytics result looks wrong

Verify ROI, flow direction, video quality, and calibration first. Compare multiple recordings before tuning detector parameters.

If a result looks stale or incomplete, use a unique video filename and inspect
the matching cache. Cancellation and truncated input can leave partial work, and
cache identity currently relies heavily on the video filename stem.

### Board connection is lost while a pump may be active

Treat the physical output as **unknown** until verified. A missing acknowledgement does not prove the pump is OFF.

## 13. Safety

FluidicStudio is laboratory control software, not a certified safety controller.

- Power down before inserting/removing pump drivers.
- Never assume a configuration file proves what is physically installed.
- Stay within the effective ranges appropriate to the real hardware/setup.
- Treat lost communication conservatively.
- Treat an unacknowledged OFF/POFF as an unknown physical state.
- Do not assume the proprietary DNX64 runtime is included with the repository.
- Keep a safe way to stop/disable the physical experiment.

Read [Hardware Limitations](HARDWARE_LIMITATIONS.md) before unfamiliar hardware work.
