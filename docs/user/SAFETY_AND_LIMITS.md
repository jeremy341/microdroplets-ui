# Safety and current limits

FluidicStudio is a control surface, not a guarantee that a connected device is
in a safe state. The physical system remains authoritative.

## Treat output state as unknown when

- the serial reader stops or the connection closes unexpectedly;
- a pump OFF command is not acknowledged;
- the application is being terminated during a command;
- a camera or board is disconnected while a worker is active.

Physically verify the setup before restarting or reconnecting. Do not assume a
failed UI action turned a pump off.

## Current product boundaries

- Automatic driver detection is experimental and must not be used as the sole
  basis for a safety decision.
- Driver/channel configuration is software configuration, not proof of the
  connected hardware model.
- Valves are a placeholder area.
- Analytics currently targets recorded video; single-photo Analytics is future
  work.
- Dino-Lite controls depend on the externally installed DNX64 runtime and the
  exact camera model/driver combination.
- Displayed ranges and vendor exports are not equivalent to complete hardware
  validation.

## Diagnostics can change state

Some tools described as probes or smoke tests initialize cameras, alter camera
settings, send sensor stream commands, or write image output. Read the tool
description before running one on a live experiment. Use a disconnected or
safe test setup whenever possible.

## Evidence labels

- **Software-tested**: covered by the committed automated tests.
- **Bench-validated**: exercised against the stated physical hardware and
  recorded in a validation result.
- **Vendor-described**: documented by the vendor/API, not necessarily verified
  on this setup.
- **Planned**: not current product behavior.
