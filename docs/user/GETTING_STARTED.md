# Getting started

## Requirements

- Windows 10/11 is the supported desktop target.
- Python 3.11–3.13 is the documented development/runtime range.
- A Bartels mp-Multiboard2 and/or a supported Dino-Lite camera are optional for
  source-only or UI work, but hardware features cannot be meaningfully verified
  without the relevant device.
- Dino-Lite DNX64 support requires the separately installed vendor runtime. It
  is proprietary, ignored by Git, and is not bundled in this repository.

## Install and launch

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python app.py
```

For development and source-only checks:

```powershell
python -m pip install -r requirements-dev.txt
pytest -q
```

The test suite is environment-sensitive: PyQt and hardware-dependent tests may
be skipped when their dependencies or devices are unavailable. A passing local
run is not the same as a bench validation.

## First safe session

1. Connect only the hardware required for the experiment.
2. Confirm the selected serial port and driver configuration.
3. Start with outputs off and verify sensor values before enabling a pump.
4. Treat a lost serial connection as an unknown physical output state.
5. Stop outputs and wait for the application’s acknowledgement before closing.

Session save/load is intended for an idle application. Saving or loading is
blocked while logging, recording, or pump/wave outputs are active.

## Where generated files go

Captures, sensor logs, sessions, diagnostics and Analytics exports belong under
`user_data/`. Do not commit experiment data or proprietary vendor binaries.
