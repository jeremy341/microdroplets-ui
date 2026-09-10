# Hardware Limitations and Validation Status

This document separates three ideas that are easy to confuse:

1. what a manufacturer says the hardware can do;
2. what FluidicStudio currently allows in software;
3. what has actually been validated on the lab setup.

A code path existing does not prove the attached hardware has been bench-tested.

## 1. Pump-driver limitations

### Shared properties

CH1–CH4 share Driver 0 / F0. They are independently switchable and have independent amplitude, but they do not have independent driver frequency or carrier mode.

If CH1 and CH2 are displayed together and F0 changes, both frequency controls must show the same acknowledged value.

### Current software ranges

| Driver type | Amplitude | Frequency used by normal UI | Carrier waveform |
| --- | ---: | ---: | --- |
| mp-Highdriver4 | 10–250 Vpp | 50–800 Hz | supported |
| mp-Highdriver | 10–250 Vpp | 50–800 Hz | supported |
| mp-Lowdriver | 0–150 Vpp | 8–800 Hz | not exposed |
| mp-Driver | 85–250 Vpp | 25–226 Hz | not exposed |

The Lowdriver electronics have a wider frequency capability than the normal application policy. FluidicStudio intentionally limits the ordinary control range rather than exposing every hardware extreme.

### Quantization

The Highdriver4 capability model uses a 5-bit amplitude quantization model for generated waves. A requested mathematical waveform may therefore produce repeated or rounded hardware steps.

The public Multiboard amplitude command is still treated as integer Vpp. Do not invent fractional-Vpp serial commands to match an internal hardware bit depth.

### Carrier/signal mode

Carrier mode is a driver-wide property and is changed only while the complete driver group is idle. Frequency is different: it is intentionally a live shared property and may be changed while channels are active.

## 2. Physical output state after communication loss

A serial exception or missing acknowledgement does **not** prove a pump is OFF.

The pump backend retains ownership when it cannot confirm a safe rollback/stop. A disconnected board must be treated as an **unknown physical output state** until the hardware is verified.

Do not automatically resume old pump or wave outputs after reconnect or session restore.

## 3. Driver configuration is not detection

`data/driver_config.json` describes what software expects to be installed. It does not interrogate or modify hardware.

Automatic driver detection is intentionally not authoritative yet. The existing diagnostic probe records opaque firmware fingerprints only. See [Driver Detection](DRIVER_DETECTION.md).

## 4. CH5 and CH6 validation

The software capability model supports the configured CH5/CH6 variants, but future maintainers should not equate that with complete bench validation of every physical variant.

Before expanding public support:

- test the real module on the real Multiboard;
- verify safe amplitude/frequency ranges;
- verify start/stop acknowledgements;
- verify carrier support assumptions;
- add hardware-backed regression notes/tests.

## 5. Sensor limitations

FluidicStudio performs one protocol-boundary conversion for liquid flow from firmware mL/min to UI/logging µL/min.

The software does not make an out-of-range physical sensor safe. Verify the manufacturer's operating range for the installed sensor and the expected experiment flow before testing.

Graph history, CSV logging, and hardware sampling are separate concerns. Pausing the graph does not pause the physical stream or logging.

## 6. Camera limitations

The validated application presets for the Dino-Lite AM4113T(R9) are:

```text
1280×1024: 10 or 20 FPS
640×480:   10, 20 or 30 FPS
```

The application deliberately does not probe every possible DirectShow mode at startup because repeated graph rebuilds can freeze or destabilize camera startup.

Focus and optical magnification remain manual.

### LED control

The UI exposes LED ON/OFF. LED intensity/FLC is **not** exposed because it was not sufficiently reliable/validated for the current setup. A low-level SDK method remaining in the backend is not permission to restore the slider without real-hardware validation.

### DNX64

OpenCV preview can work while DNX64 controls fail. This means the video path and control path must be debugged separately.

The DNX64 files are vendor software. Review `vendor/dnx64/License.txt` and vendor redistribution terms before publishing them outside the intended project/lab context.

## 7. Analytics limitations

Analytics currently accepts recorded **video**, not a standalone still image.

Without optical calibration:

- length remains pixels;
- velocity remains pixels/second.

With calibration, scale must match the source resolution and optical setup. A microscope magnification label by itself is not an automatic `µm/px` calibration.

A counted physical event can remain valid as an event even when its geometry is rejected. Do not simplify `counted` and `valid geometry` into one flag.

## 8. Valves

Valve control is not implemented in this release. The Valves page is a reserved placeholder and must not be presented as a functional hardware subsystem.

## 9. Operating safety boundary

FluidicStudio is laboratory control software, not a certified safety controller.

- Power the Multiboard down before inserting/removing pump-driver hardware.
- Verify tubing and electrical setup before enabling outputs.
- Do not assume a configuration file proves what is physically installed.
- Keep a safe way to stop the experiment outside the application when appropriate.
- Treat unacknowledged commands and lost connections conservatively.


## Related future work

See [Future Roadmap](FUTURE_ROADMAP.md) for planned validation, driver detection, valves, camera features and packaging work.
