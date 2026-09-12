# Future Roadmap and Handoff Backlog

This document separates current release behavior from future work that has been
discussed or is clearly implied by the current architecture.

Nothing listed here should be treated as already implemented unless the status
explicitly says so.

## Priority legend

```text
P0  safety/correctness before broader deployment
P1  high-value next development
P2  useful expansion
P3  exploratory / optional
```

## 1. Automatic pump-driver detection — P1

**Status:** research/probe tooling exists; production selection remains static.

Current source of truth:

```text
data/driver_config.json
```

Existing evidence tooling:

```text
tools/driver_detection_probe.py
tools/compare_driver_probes.py
```

Known observed fingerprints from earlier bench work include different `Driver:`
text for a Highdriver4-installed setup vs no-driver setup, but the encoding is
not documented enough to hard-code a mapping.

Next steps:

1. gather repeated labelled captures for every physically known driver setup;
2. repeat across firmware versions/boards if possible;
3. determine whether fingerprint is stable and unambiguous;
4. create an evidence table/versioned mapping;
5. implement detection as a separate capability layer;
6. retain manual configuration/fallback when detection is uncertain;
7. never silently choose a high-voltage capability from an unverified token.

See [Driver Detection](DRIVER_DETECTION.md).

## 2. Valve implementation — P1/P2

**Status:** UI placeholder only.

Before implementation, determine:

- exact connected valve hardware;
- documented Multiboard commands;
- channel/range semantics;
- safe startup/shutdown defaults;
- whether valve actions need ACK/rollback;
- whether Workspace should ever expose valves.

Do not copy pump assumptions into valve control.

## 3. Still-image Analytics — P1

**Status:** planned.

Goal: allow a photo to be analyzed for geometry where temporal data is not
required.

Reuse:

- detector;
- ROI;
- calibration;
- geometry/refinement;
- overlay rendering.

Do not fabricate:

- velocity;
- generation rate;
- temporal spacing.

The UI should clearly indicate which metrics are unavailable for a still image.

## 4. Analytics V66 / real-video benchmark program — P1

**Status:** current detector works but further accuracy work is expected.

Initial benchmark recordings used during development:

```text
recording_20260820_164623(1).mp4
recording_20260820_164740.mp4
recording_20260820_172002.mp4
```

Recommended process:

1. freeze current baseline output;
2. annotate/verify ground truth or high-confidence reference measurements;
3. classify failures (missed event, duplicate, false positive, bad boundary,
   bad tracking, bad geometry, bad temporal assignment);
4. change the pipeline stage that causes the failure;
5. compare all recordings, not one successful clip;
6. preserve human-readable rejection reasons.

The recordings and result manifests are not tracked, so this historical
benchmark is not reproducible from a clean clone. Label it historical/external
evidence until the retained benchmark artifacts are available.

## 5. Broader sensor support — P2

Protocol definitions exist for pressure, gas flow, analog and some
firmware-dependent sensors, but current hardware validation is centered on the
liquid-flow path.

Future sensor integration should follow [Sensor Pipeline](SENSOR_PIPELINE.md)
and prove raw reply formats before adding polished UI cards.

## 6. Driver/hardware validation matrix — P1

The configured software models support:

- Highdriver4;
- Highdriver;
- Lowdriver;
- mp-Driver.

The release should eventually maintain a matrix of:

```text
driver type
physical board/slot
channel
amplitude range verified?
frequency range verified?
carrier mode verified?
Wave timing verified?
firmware version
```

This prevents “implemented capability” from being confused with “bench
validated on this exact hardware.”

The working matrix is now documented at
[developer/HARDWARE_VALIDATION_MATRIX.md](developer/HARDWARE_VALIDATION_MATRIX.md).

## 7. DNX64 feature expansion — P2/P3

The vendor wrapper exposes more than the current UI.

Candidate features **only for compatible cameras and after validation**:

- MicroTouch → photo capture shortcut;
- AMR → magnification metadata / calibration assistance;
- FOVx → possible measurement calibration aid;
- FLC quadrant/level control;
- AXI/Aim Point controls;
- motorized focus/EDOF-related lens positioning;
- Wi-Fi camera backend.

See [DNX64 Reference](DNX64_REFERENCE.md) before implementing any of these.

### Explicit current decision

LED intensity/FLC must **not** simply be re-added to the existing Camera UI. The
previous generic intensity control was removed because it was not sufficiently
validated on the current camera setup.

## 8. Workspace presets — P2

Candidate presets discussed during development:

```text
Pumps + Sensors
Wave + Sensors
Camera + Sensors
```

The current Workspace already supports compact panels and a draggable split.
Presets should configure views, not create duplicate backends.

## 9. “Analyze latest recording” — P2

Candidate workflow:

```text
Camera recording finishes
→ expose latest capture path
→ one action opens/queues it in Analytics
```

Keep capture and Analytics as separate subsystems; share the file reference, not
the camera runtime.

## 10. Quick switch / Ctrl+K — P3

A command/quick-navigation surface was discussed as a future UX improvement.
Potential actions:

- navigate to page;
- select Workspace preset;
- open latest capture;
- start common non-dangerous UI workflows.

Avoid putting irreversible hardware actions behind fuzzy-search execution without
clear confirmation/state feedback.

## 11. Packaging / installer — P1 before public release

Current release is a Python project, not a polished signed installer.

Future packaging work should cover:

- virtual-environment/dependency strategy or frozen executable;
- PyQt/OpenCV packaging;
- writable runtime-data location;
- serial driver expectations;
- DNX64 dependency discovery;
- Windows camera permissions/DirectShow behavior;
- clean upgrade/migration path;
- crash logs.

## 12. Vendor redistribution/legal review — P0 before public binary distribution

The repository does not track the proprietary DNX64 runtime. Before distributing
an installer/package that supplies vendor DLLs and their original license/readme,
verify that the signed/vendor SDK agreement permits the intended redistribution.

Do not remove the original vendor notice files.

## 13. Shutdown/reconnect hardening — P0/P1

The current backend already blocks normal disconnect if `POFF` is not
acknowledged. Future robustness work can add clearer recovery UX for:

- USB cable removal mid-operation;
- serial driver reset;
- camera hot unplug while recording;
- reconnect state reconciliation.

Never auto-resume pumps or Waves after reconnect.

## 14. Session identity improvements — P2

Current board profile matching relies mainly on last-known port/display name
because the app does not have a robust unique Multiboard identity from the
commands used.

If a reliable serial number/device identity becomes available, add it to session
matching while keeping safe migration for old sessions.

## 15. User documentation screenshots — P2

The User Guide is currently text-oriented. A future polished release could add
annotated screenshots for each page, but screenshots should not replace the
behavioral page docs because UI layout changes faster than hardware rules.

## 16. Test/CI improvements — P2

Potential improvements:

- dedicated CI workflow for backend tests;
- headless Qt smoke test environment;
- static lint/type checks;
- artifacted test report;
- optional manually-triggered hardware validation checklist.

Real hardware should remain an explicit validation layer rather than being
faked by a green CI badge.

## 17. Documentation maintenance rule

When implementing roadmap items:

1. move status from planned → implemented/validated;
2. update the relevant specialist MD;
3. update page docs if UI changed;
4. add tests;
5. remove obsolete roadmap text instead of leaving contradictory plans.

Use the audience indexes (`docs/user/INDEX.md` and `docs/developer/INDEX.md`) as
the stable navigation entry points, and keep evidence labels explicit when a
feature moves between planned, software-tested and bench-validated status.
