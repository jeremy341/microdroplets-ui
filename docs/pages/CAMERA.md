# Camera Page

The Camera page is the complete UI for camera discovery, live preview, capture, recording, and validated Dino-Lite controls.

For backend/threading details, read [Camera Engine](../CAMERA_ENGINE.md).


For runtime architecture see [Camera Engine](../CAMERA_ENGINE.md); for every DNX64 feature exposed by the bundled wrapper and its integration status see [DNX64 Reference](../DNX64_REFERENCE.md).

## Main controls

The page includes:

- camera device selection;
- Search;
- Open Capture Folder;
- Start/Stop Camera;
- live preview with measured FPS overlay;
- Capture photo;
- Record / Stop recording;
- resolution;
- FPS;
- brightness;
- automatic/manual exposure;
- LED ON/OFF;
- manual focus/magnification note;
- most recent capture indicator.

## Video presets

For the current AM4113T(R9) profile:

```text
1280×1024 → 10, 20 FPS
640×480   → 10, 20, 30 FPS
```

The application does not expose arbitrary FPS values for this profile.

## Exposure

With Auto enabled, manual exposure is disabled. With Auto off, the slider controls the practical validated percentage mapping implemented by the camera backend.

Property writes are queued/throttled so slider interaction does not block Qt's GUI thread.

## Brightness

Brightness is presented as a percentage even though underlying driver representations can differ. The backend handles scale/readback details.

## LED

The product exposes only LED ON/OFF.

There is intentionally **no LED intensity/FLC slider**. Intensity was removed because it was not sufficiently reliable on the validated setup.

## Focus and magnification

Focus and optical magnification are manual on the current camera/setup. The application does not pretend to control them.

## Capture paths

Photos and videos are stored under:

```text
user_data/captures/
```

Use Open Capture Folder from the page to jump to the directory.

## Workspace relationship

Camera Workspace uses the same camera runtime and latest frame. It does not open a second camera stream.

The Workspace preview may crop the **displayed preview** to fill the compact panel. This does not crop the original frame used for recording/capture.

## If preview works but controls do not

Treat OpenCV and DNX64 as separate paths:

1. verify preview/video path;
2. run `tools/dnx64_probe.py`;
3. verify DNX64 DLL/device attachment;
4. inspect control readback.

Do not repeatedly reopen the video stream as the first response to a control-only failure.

## Relevant tests

```text
test_camera_service.py
test_camera_profiles.py
test_fps_profiles.py
test_camera_exposure_validated_range.py
test_camera_led_controls.py
test_workspace_camera.py
```

## Navigation

```text
Shift+5
```
