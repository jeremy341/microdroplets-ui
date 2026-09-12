# Camera Engine

This document describes the camera runtime shared by the full Camera page and Camera Workspace panel.

## 1. Design goal

There must be **one camera runtime**, even when the camera is visible in multiple UI places.

```text
Dino-Lite / USB camera
        ↓
OpenCVCamera + CameraWorker
        ↓
shared latest frame / camera state
       ↙ ↘
Camera page   Camera Workspace
```

Workspace must never open “camera #2” simply to render its own preview.

## 2. Two cooperating hardware paths

FluidicStudio intentionally separates video from Dino-Lite-specific controls.

### OpenCV / DirectShow

Used for:

- opening the video device;
- frame acquisition;
- resolution/FPS changes;
- photo capture;
- video recording;
- generic camera-property fallback where appropriate.

On Windows, `backend/camera_service.py` prefers `cv2.CAP_DSHOW`.

### DNX64

Used when available for Dino-Lite-specific control/identification:

- stable device enumeration/IDs;
- brightness;
- auto exposure;
- manual exposure;
- LED ON/OFF.

DNX64 is optional for basic frame preview. Therefore “preview works, exposure does not” is a valid diagnostic state.

## 3. Main files

| File | Role |
| --- | --- |
| `backend/camera_service.py` | primary camera abstraction, OpenCV + DNX64 coordination |
| `backend/dnx64_api.py` | small ctypes wrapper around the required DNX64 API; keep its relationship to production explicit |
| `backend/dnx64_vendor.py` | current vendor-derived low-level wrapper used by the camera service |
| `backend/camera_profiles.py` | persistent/per-device camera profile support |
| `backend/fps_profiles.py` | FPS profile helpers |
| `ui/pages/Camera.py` | full camera UI, workers and UI-side orchestration |
| `ui/pages/Workspace.py` | compact view of the same Camera runtime |
| `tools/dnx64_probe.py` | DNX64 attachment diagnostic |
| `tools/camera_backend_smoke_test.py` | backend smoke test |

For the complete DNX64 function inventory and feature-support matrix, see [DNX64 API Reference](DNX64_REFERENCE.md).

## 4. Camera discovery

`enumerate_cameras()` first tries DNX64 enumeration when available. DNX64 gives stable Dino-Lite names/device IDs. OpenCV may then probe the actual video handle and report mode information.

During hot-plug refreshes, the code can use DNX64 enumeration without opening a second DirectShow handle. This avoids disturbing an active preview.

## 5. Opening a device

`OpenCVCamera.open()` performs approximately:

```text
close previous runtime
→ OpenCV VideoCapture
→ attach DNX64 controller
→ if DNX64 first attach fails, read one frame and retry once
→ set requested video mode
→ build capabilities
→ cache initial control values
```

The retry exists because some driver/runtime combinations attach more reliably after the video stream is active.

Controls should not repeatedly initialize DNX64 on every slider event.

## 6. Supported UI video presets

For the documented AM4113T(R9) profile (the profile is not a substitute for a
current bench result):

```text
1280×1024 → 10, 20 FPS
640×480   → 10, 20, 30 FPS
```

The application uses known presets instead of cycling through every possible resolution/FPS combination at startup. Repeated DirectShow property changes can rebuild the capture graph and previously caused noticeable UI stalls.

## 7. Frame ownership and Workspace synchronization

`OpenCVCamera` stores a thread-safe latest decoded frame. The full Camera page keeps its `last_frame` updated even when hidden, while avoiding unnecessary pixmap rendering when not visible.

Camera Workspace reads the same runtime/latest frame. This fixed the historical problem where Workspace preview appeared only after first visiting the Camera tab.

The compact Workspace preview uses a center-cropped “cover” presentation so the frame fills the preview area without stretching. This crop is **display-only**; the underlying captured/recorded frame remains unchanged.

## 8. Threading model

Camera operations that can block must stay away from Qt's GUI thread.

`ui/pages/Camera.py` contains workers for responsibilities such as:

- discovery;
- camera open;
- continuous frame acquisition;
- photo save;
- recording.

Property writes are queued to the camera worker. Exposure dragging is throttled so the physical camera receives live feedback without sending a blocking write for every mouse pixel.

When modifying camera controls, preserve this rule:

> UI events request work; worker/backend code performs blocking camera operations.

## 9. Exposure behavior

The UI uses a practical percentage mapping rather than exposing raw DNX64/DirectShow numeric ranges directly.

Manual exposure is enabled only when Auto is off. Mode changes are applied before the manual exposure value because DirectShow can asynchronously re-enable/overwrite exposure behavior during a mode transition.

The slider uses throttled live writes and a final readback/commit when interaction finishes.

The current implementation audit found that auto-exposure enable can report
success without a reliable readback, and the production exposure mapping is
quadratic while some diagnostic tools use a linear mapping. Keep those paths
separate in tests and do not infer camera calibration from the UI percentage.

## 10. Brightness

Brightness handling remembers the discovered representation/scale. A readback of zero is ambiguous because zero is valid in multiple driver scales; the backend therefore does not repeatedly reinterpret the scale after it has been learned.

The DNX64 brightness binding also needs exact-device verification: the current
ctypes call path can pass the first range value with the wrong pointer/value
shape, causing fallback ranges or failed writes on real hardware even when fake
tests pass.

## 11. LED

The product UI exposes only:

```text
LED ON / OFF
```

A low-level FLC/intensity function remains in `camera_service.py` for future research, but the intensity slider was deliberately removed from the product because it was not reliably validated with the current camera/runtime.

Do not reintroduce LED intensity without testing real hardware first.

## 12. Capture and recording

Photos and recordings are written below:

```text
user_data/captures/
```

Recording uses a separate recorder worker. If frame production outruns writing, old queued frames may be dropped to keep latency bounded rather than allowing an unlimited queue to grow.

Changing resolution/FPS while recording stops the recording before applying the new mode.

Known lifecycle limitations include startup/stop races, worker reads after a
camera close or replacement, swallowed read exceptions, and a recorder object
that may remain attached after a writer failure. A stop request that times out
must not be treated as proof that the worker has exited.

## 13. DNX64 runtime resolution

DNX64 contains substantially more vendor functionality than the current Camera UI exposes. Do not infer UI support from the existence of a vendor wrapper method; consult [DNX64 API Reference](DNX64_REFERENCE.md) first.


The backend can resolve an externally installed/locally supplied DNX64 runtime
from:

1. the `DNX64_DLL` environment variable;
2. the project-local ignored `vendor/dnx64/DNX64.dll` when supplied by the user;
3. an installed DNX64 location.

Keep the vendor notices/license with the runtime. Verify redistribution terms
before a public installer/repository distribution. The repository does not
contain the proprietary DLL.

## 14. Debugging order

### No camera appears

1. verify Windows sees the device;
2. run `tools/camera_backend_smoke_test.py`;
3. check DirectShow/OpenCV access;
4. check whether another application has the video handle open.

### Preview works but controls fail

1. run `tools/dnx64_probe.py`;
2. verify DNX64 DLL resolution;
3. verify DNX64 selected device/index;
4. inspect attachment/readback rather than repeatedly reopening OpenCV.

### Exposure behaves differently by resolution

Use:

```text
tools/exposure_diagnostic.py
tools/exposure_resolution_diagnostic.py
```

Do not widen UI ranges merely because a raw driver reports a larger ceiling.

The diagnostic defaults also need care: a 1280×1024 smoke configuration using
30 FPS is outside the documented production preset, so diagnostic success/failure
must not be interpreted as a product-mode result.

## 15. Tests to keep green

Relevant tests include:

```text
test_camera_service.py
test_camera_profiles.py
test_fps_profiles.py
test_dnx64_camera_backend.py
test_dnx64_attach_recovery.py
test_camera_exposure_validated_range.py
test_camera_performance_architecture.py
test_camera_led_controls.py
test_workspace_camera.py
```
