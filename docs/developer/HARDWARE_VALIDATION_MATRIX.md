# Hardware validation matrix

This document is the boundary between what the repository implements and what
has actually been exercised on hardware. Update it with a date, device model,
firmware/driver version, test command or procedure, and result whenever a bench
claim is made.

| Area | Current repository evidence | Bench evidence required | Current status |
| --- | --- | --- | --- |
| Multiboard serial framing and parser | Automated parser/serial tests | Board capture with firmware context | Software-tested; bench result not recorded here |
| Pump ON/OFF and rollback | Unit/integration coverage plus ACK code | Each driver/channel, timeout and disconnect cases | Software-tested; physical safety behavior remains hardware-dependent |
| Shared driver frequency/carrier | Configuration and state tests | CH1–CH4 and F0/F1/F2 combinations | Implemented; bench matrix needed |
| Sensor parsing and units | Parser/history tests | Each enabled sensor on the target board | Software-tested; device coverage incomplete |
| CSV logging | Logger tests | Dynamic sensor availability and long-running stop | Software-tested; edge cases remain documented |
| OpenCV camera preview | Camera service tests | Camera model, resolution, FPS and reconnect | Software-tested; bench result needed |
| DNX64 brightness/exposure/LED | Wrapper/service paths | Exact camera model and installed vendor runtime | Vendor-described/implemented; not a complete bench validation |
| Video Analytics | Offline tests and fixtures | Representative recorded videos with retained outputs | Software-tested; historical benchmark files are not tracked |
| Driver auto-detection | Probe code | Known boards with false-positive/negative captures | Planned/experimental |
| Valves | Placeholder page | Hardware implementation and safety procedure | Planned |

## Rules

- A missing vendor DLL is an environment prerequisite, not a reason to commit
  the proprietary file.
- A software configuration entry does not prove the connected driver model.
- Historical V65/V66 statements are not reproducible evidence unless the source
  recordings, manifests, and result artifacts are retained outside the code
  repository and referenced here.
- Diagnostics that initialize hardware or change settings must be labeled as
  state-changing even when they are called “probe” or “smoke test”.
