# Dino-Lite DNX64 API Reference and Integration Status

This document inventories the DNX64 functionality represented by the bundled
FluidicStudio wrapper and explains what is actually used by the product.

It exists so a future developer does not expose a Dino-Lite feature merely
because a ctypes method happens to exist.

## 1. What DNX64 is used for in FluidicStudio

FluidicStudio intentionally separates two responsibilities:

```text
video frames / resolution / FPS / recording
        → OpenCV + Windows DirectShow

Dino-Lite identity and device-specific controls
        → DNX64
```

The official Dino-Lite Python repository also expects users to set the device
index before performing operations and uses OpenCV for preview examples. The
vendor SDK/runtime may be distributed separately from the public Python wrapper.

## 2. Files in this project

| File | Purpose |
| --- | --- |
| `backend/dnx64_vendor.py` | low-level vendor-derived ctypes wrapper; broad API inventory |
| `backend/dnx64_api.py` | smaller typed wrapper for core production functions |
| `backend/camera_service.py` | production policy, DNX64 attachment/readback and OpenCV coordination |
| `vendor/dnx64/DNX64.dll` | vendor runtime |
| `vendor/dnx64/DNX32.dll` | vendor runtime dependency retained beside DNX64 |
| `vendor/dnx64/libusbK.dll` | vendor runtime dependency |
| `vendor/dnx64/License.txt` | vendor license text |
| `vendor/dnx64/ReadMe.txt` | vendor runtime notes |
| `tools/dnx64_probe.py` | read-only attachment/identity diagnostic |

## 3. Runtime resolution order

The production resolver checks, in order:

1. `DNX64_DLL` environment variable;
2. project-local `vendor/dnx64/DNX64.dll`;
3. `C:\Program Files\DNX64\DNX64.dll`.

The camera service may also retry another candidate if one runtime is present but
cannot initialize on the current driver stack.

## 4. Important vendor runtime notes

The bundled vendor note says the target setup needs:

```text
DNX64.dll
DNX32.dll
libusbK.dll
```

It also warns that other built-in/external webcams may interfere with sensor or
LED control and suggests disabling/unplugging them if necessary.

Keep the original vendor notice/license files unchanged.

## 5. DNX64 initialization behavior

`_Dnx64Controller.initialize()` deliberately does not trust only the boolean
returned by `Init()`.

Some tested runtime combinations can return a false-ish `Init` result while the
device becomes available immediately afterward. FluidicStudio therefore treats
successful enumeration/identity/control readback as stronger evidence.

Production readiness sequence:

```text
load DLL
→ patch known ctypes signature mismatch
→ SetVideoDeviceIndex(index)
→ call Init()
→ GetVideoDeviceCount()
→ GetVideoDeviceName()
→ optionally verify exposure controls by read/write/readback
```

Do not remove the short delays/readback checks without hardware testing.

## 6. Current product-exposed DNX64 features

### Device enumeration/identity

Used for:

- device count;
- selected device index;
- stable Dino-Lite device name;
- device ID;
- vendor configuration mask.

### Brightness

Uses the video-processing-amplifier API:

```text
GetVideoProcAmpValueRange(0)
GetVideoProcAmp(0)
SetVideoProcAmp(0, value)
```

UI values are normalized to 0–100%, but the raw hardware range is discovered
from DNX64.

### Auto exposure

```text
GetAutoExposure(index)
SetAutoExposure(index, 0/1)
```

Manual exposure is only meaningful when Auto is disabled.

### Manual exposure

```text
GetExposureValue(index)
SetExposureValue(index, value)
```

FluidicStudio maps a practical UI percentage to a validated raw range rather
than exposing the entire theoretical driver range directly.

### LED ON/OFF

```text
SetLEDState(index, 0/1)
```

The vendor wrapper notes that LED control requires the preview to be established
and is not applicable to some camera families.

FluidicStudio currently exposes **LED ON/OFF only**.

## 7. API inventory and product status

The following table covers methods represented by `backend/dnx64_vendor.py`.
“Present” means the wrapper has a method; it does not automatically mean the
connected AM4113T supports it.

| API | Purpose | Feature dependency | FluidicStudio status |
| --- | --- | --- | --- |
| `Init` | initialize DNX64 control object | none | production-used |
| `GetVideoDeviceCount` | enumerate video devices | none | production-used |
| `GetVideoDeviceIndex` | read selected device index | none | used by typed wrapper/diagnostics |
| `SetVideoDeviceIndex` | select target device | none | production-used; must happen early |
| `GetVideoDeviceName` | device display name | none | production-used |
| `GetDeviceId` | stable device ID | none | production-used |
| `GetDeviceIDA` | alternate/ANSI ID | none | present, not product-required |
| `GetConfig` | vendor device capability/config bit mask | model-dependent | production-used for capability hints |
| `GetVideoProcAmp` | read generic video property | property support varies | production-used for brightness index 0 |
| `GetVideoProcAmpValueRange` | read property min/max/step/default | property support varies | production-used for brightness |
| `SetVideoProcAmp` | set generic video property | property support varies | production-used for brightness |
| `GetAutoExposure` | read auto-exposure state | camera support | production-used |
| `SetAutoExposure` | set auto-exposure state | camera support | production-used |
| `GetExposureValue` | read exposure value | camera support | production-used |
| `SetExposureValue` | set exposure value | camera support | production-used |
| `GetAETarget` | read auto-exposure target | camera/SDK support | present, not integrated |
| `SetAETarget` | set AE target | camera/SDK support | present, not integrated; vendor wrapper mentions a range but verify current SDK docs before use |
| `SetLEDState` | LEDs on/off | Dino-Lite model; preview requirement | production-used |
| `SetFLCSwitch` | select/toggle FLC quadrant | FLC model | present, not exposed |
| `SetFLCLevel` | FLC luminance level 1–6 | FLC model | low-level support exists; intentionally not exposed |
| `SetEFLC` | per-quadrant extended FLC value | EFLC-capable model | present, unvalidated |
| `GetAMR` | automatic magnification reading | AMR model | present, not integrated |
| `FOVx` | field of view from magnification | model/SDK support | present, not integrated |
| `EnableMicroTouch` | enable MicroTouch | MicroTouch model | present, not integrated |
| `SetEventCallback` | callback for MicroTouch event | MicroTouch model | present, not integrated/unvalidated |
| `SetAimpointLevel` | aim-point laser level | APL model | present, not integrated |
| `SetAXILevel` | axial illumination level | AXI model | present, not integrated |
| `GetLensPosLimits` | read lens-position limits | EDOF/focus motor model | present, not integrated/unvalidated |
| `GetLensFinePosLimits` | read fine lens-position limits | EDOF/focus motor model | present, not integrated/unvalidated |
| `SetLensInitPos` | initialize lens position | EDOF/focus motor model | present, not integrated |
| `SetLensPos` | move lens position | EDOF/focus motor model | present, not integrated |
| `SetLensFinePos` | fine lens move | EDOF/focus motor model | present, not integrated |
| `GetWiFiImage` | obtain image from Wi-Fi path | Wi-Fi device/runtime | wrapper method exists, not production-ready |
| `GetWiFiVideoCaps` | enumerate Wi-Fi resolutions | Wi-Fi device/runtime | present, not integrated |
| `SetWiFiVideoRes` | set Wi-Fi resolution | Wi-Fi device/runtime | wrapper method exists, not production-ready |

## 8. Feature families a future developer may encounter

### FLC — Flexible LED Control

Dino-Lite describes FLC as allowing quadrant switching and six luminance levels
on supported models.

FluidicStudio currently has backend pieces for:

```text
SetFLCSwitch
SetFLCLevel
SetEFLC
```

but the product intensity control was removed because it was not sufficiently
validated on the current setup. Do not re-add an intensity slider solely because
`GetConfig()` advertises an FLC bit.

Required future validation:

1. identify exact camera model/config mask;
2. establish preview;
3. verify which quadrants respond;
4. verify level 1–6 behavior and readback/visual response;
5. decide whether OFF is `SetLEDState(0)` or an FLC-specific state;
6. add tests/diagnostic tool before exposing UI.

### AMR — Automatic Magnification Reading

`GetAMR()` can provide magnification on models with AMR. This could eventually
feed calibration/measurement workflows, but it is not currently connected to
Analytics calibration.

### FOVx

`FOVx(device_index, magnification)` returns a field-of-view value from the SDK
wrapper. It is not used today. A future measurement feature must validate units
and compatibility on the exact camera before using it as a calibration source.

### MicroTouch

The wrapper contains:

```text
EnableMicroTouch
SetEventCallback
```

A future integration could map the microscope touch button to photo capture or
another safe action. The callback binding is not currently production-tested.

### EDOF / motorized lens APIs

Lens position methods exist for compatible hardware. The public Dino-Lite
feature name EDOF usually refers to extended depth-of-field workflows, while the
wrapper exposes lower-level lens-position operations. FluidicStudio does not
currently implement focus stacking or motor focus.

### AXI / Aim Point

The wrapper contains `SetAXILevel` and `SetAimpointLevel` for models that support
those illumination/aiming features. They are not applicable to all cameras and
are not integrated.

### Wi-Fi APIs

The vendor wrapper has Wi-Fi image/capability/resolution methods, but the current
FluidicStudio camera architecture targets a USB Dino-Lite with DirectShow.
Treat Wi-Fi support as a separate future backend, not as a small checkbox added
to the existing USB path.

## 9. Known wrapper caveats

### `SetVideoProcAmp` signature mismatch

The vendor-derived Python wrapper historically configures a one-argument ctypes
signature, while the SDK header/runtime path used here expects:

```text
SetVideoProcAmp(property_index, new_value)
```

`camera_service.py` patches the ctypes signature after loading the DLL. Do not
remove this patch unless the bundled wrapper/runtime has been replaced and
verified.

### Broad vendor wrapper vs production wrapper

`dnx64_vendor.py` is intentionally close to the vendor API and contains methods
that have not been tested in FluidicStudio. `dnx64_api.py` is a narrower typed
wrapper for core operations.

Application behavior belongs in `camera_service.py`, not in the vendor wrapper.

### Unconfigured/unvalidated exports

Some convenience methods in the vendor-derived wrapper are not represented in
`METHOD_SIGNATURES` or have not been exercised on the current runtime. Before
using a currently-unused API:

1. verify the current DNX64 header/export signature;
2. set explicit `argtypes`/`restype`;
3. build a standalone read-only or reversible probe;
4. test with the exact model;
5. only then integrate into the product.

## 10. `GetConfig` and capability masks

FluidicStudio currently uses the vendor `config` value as a **hint**, for example
checking bit `0x02` for FLC support in `camera_service.py`.

A capability bit means “the SDK/model advertises this feature,” not “our current
UI implementation has been validated.” Keep those states separate.

## 11. Camera model currently targeted

The project has primarily been developed around a Dino-Lite **AM4113T(R9)**.
Known UI video presets are documented in [Camera Engine](CAMERA_ENGINE.md).

Do not assume every DNX64 feature listed above exists on the AM4113T.

## 12. How to add a DNX64 feature

Use this order:

```text
vendor documentation/header
→ verify ctypes signature
→ standalone diagnostic/probe
→ test feature capability on hardware
→ add camera-service method + structured result
→ add worker/async handling if call can block
→ expose UI only when supported
→ add tests + update docs
```

Never perform long DNX64 calls directly in Qt paint/slider handlers.

## 13. Useful diagnostics

```powershell
python tools/dnx64_probe.py
python tools/camera_backend_smoke_test.py
```

Exposure-specific:

```powershell
python tools/exposure_diagnostic.py
python tools/exposure_resolution_diagnostic.py
```

## 14. External references

- Official Python API: `https://github.com/dino-lite/DNX64-Python-API`
- Dino-Lite SDK page: `https://www.dino-lite.com/download06.php`
- Dino-Lite advanced features: `https://www.dino-lite.com/features.php`

The public GitHub wrapper and a distributor-provided DNX64 SDK package may not
always carry the same version number at the same time. Prefer the header/runtime
that accompanies the actual SDK package being integrated.
