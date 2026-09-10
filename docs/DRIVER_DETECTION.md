# Future Pump-Driver Detection

FluidicStudio currently uses explicit configuration rather than automatic pump-driver detection. This is intentional.

The production serial boundary is documented separately in [Multiboard Communication](BOARD_COMMUNICATION.md).

## 1. Current source of truth

```text
data/driver_config.json
```

The file says which driver type software should expect for each supported driver group. It must match the physical board configuration.

Automatic detection must not silently replace this file until the firmware fingerprint mapping is sufficiently verified.

## 2. Existing reconnaissance tools

The release includes read-oriented tools:

```powershell
python tools/driver_detection_probe.py COM3 --label highdriver4_only
python tools/driver_detection_probe.py COM3 --label no_driver
python tools/compare_driver_probes.py user_data/diagnostics/driver_probe_*.json
```

`driver_detection_probe.py` intentionally treats the firmware `Driver:` response as an **opaque fingerprint**. It does not claim that individual characters or bits are understood.

The probe can use read-oriented requests such as:

```text
V
<settings / Enter response>
P1V? ... P6V?   optional
```

Diagnostic output belongs in `user_data/diagnostics/` and should not be committed as normal source data.

## 3. Bench observations so far

Known labelled tests produced different `Driver:` fingerprints for different physical configurations, including observations such as:

```text
Highdriver4 test configuration → 4D
No-driver test configuration   → D
```

These are observations, **not a universal decoder**. Firmware version, board revision, other installed modules, or undocumented encoding may change the payload.

Do not implement:

```python
if fingerprint == "4D":
    return "highdriver4"
```

as production truth based only on these examples.

## 4. Safe implementation plan

A future automatic detector should be evidence-based:

1. collect several captures for every known physical configuration;
2. label each capture with board/firmware/driver arrangement;
3. repeat power cycles and reconnects;
4. compare fingerprints for stability;
5. test more than one board if possible;
6. model results as `known`, `unknown`, or `conflict`;
7. show detected-versus-configured mismatches to the user;
8. keep unknown/conflicting hardware disabled rather than guessing;
9. never rewrite `driver_config.json` from a single detection response;
10. add hardware-backed tests before detection is allowed to enable controls.

## 5. Recommended architecture

Keep detection separate from capability policy:

```text
physical probe
    ↓
DriverFingerprint
    ↓
verified mapping database
    ↓
DetectedDriverConfiguration
    ↓
compare against configured DriverConfiguration
    ↓
UI warning / confirmation
```

`backend/driver_capabilities.py` should still define what a known driver type is allowed to do. Detection should identify hardware, not redefine ranges.

## 6. User-facing behavior when detection is added

A useful future UI should distinguish:

```text
Configured: mp-Highdriver4
Detected:   mp-Highdriver4
Status:     Match
```

from:

```text
Configured: mp-Lowdriver
Detected:   Unknown fingerprint "..."
Status:     Controls disabled until verified
```

and:

```text
Configured: mp-Lowdriver
Detected:   mp-Highdriver
Status:     Mismatch — check physical hardware/configuration
```

Do not hide this uncertainty behind a generic “driver connected” label.

## 7. Files involved

| File/tool | Responsibility |
| --- | --- |
| `data/driver_config.json` | current explicit source of truth |
| `backend/driver_config.py` | strict config loading/validation |
| `backend/driver_capabilities.py` | ranges and capabilities for known driver types |
| `tools/driver_detection_probe.py` | capture opaque board evidence |
| `tools/compare_driver_probes.py` | compare labelled probe results |
| `tests/test_driver_detection_probe.py` | regression coverage for safe probing |
| `tests/test_compare_driver_probes.py` | comparison logic coverage |
