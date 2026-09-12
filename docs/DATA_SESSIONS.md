# Runtime Data, Configuration and Sessions

This document explains what FluidicStudio stores, where it stores it and which
files are safe to edit or commit.

The path source of truth is `backend/application_paths.py`.

## 1. Version-controlled application data

```text
data/driver_config.json
data/waveforms.json
```

### `driver_config.json`

Describes the expected installed pump-driver types.

It is static configuration, **not automatic detection**. Configuration is loaded
once per process; changing it requires an application restart.

### `waveforms.json`

Stores the saved waveform library used by the Wave interface. Pumps displays
driver carrier/signal state; it does not own the saved waveform library.

## 2. Generated user/runtime data

Default root:

```text
user_data/
```

Override for development/deployment:

```text
FLUIDICSTUDIO_USER_DATA
```

Current directories:

```text
user_data/
├─ captures/       camera photos/videos
├─ sensor_logs/    sensor CSVs
├─ sessions/       saved FluidicStudio sessions
├─ diagnostics/    hardware probe/capture output
├─ exports/        exported files
└─ analytics/      cached/persisted Analytics data
```

Additional files include:

```text
user_data/analytics_settings.json
user_data/camera_profiles.json
```

## 3. Portable path philosophy

The release keeps generated data below the project root by default instead of
hard-coding a specific user's Documents path.

`ensure_runtime_directories()` creates missing directories at startup.

## 4. Legacy migration

Older builds used locations such as:

```text
~/Documents/FluidicStudio
~/Documents/log_data.csv
<project>/captures
<project>/diagnostics
```

Migration is **copy-forward only**:

- old data is copied when the new destination does not already exist;
- source files are not deleted;
- migration failures do not block application startup.

## 5. Session model

Session responsibilities live in:

```text
backend/session_manager.py
backend/session_runtime.py
```

A session can preserve experiment configuration and board profiles without
silently reactivating hardware.

Save and load are intended for an idle application. The UI blocks these actions
while logging, recording, or active pump/Wave outputs are running.

## 6. Session safety normalization

Runtime pump state is never persisted/restored as physically ON.

When a board profile is built:

```text
enabled = False
```

When it is applied:

```text
channel enabled → False
hardware_state → off (RAM assumption for not-started restored configuration)
```

A loaded session can restore staged settings but the user must explicitly start
outputs.

## 7. Board profile matching

Profiles can be matched to a discovered board by:

1. saved/last-known port when unique;
2. display name when unique.

The session format has fields for richer device identity such as vendor/product
ID and serial number, but the current Multiboard path does not provide a full
reliable hardware identity inventory through the commands used by the app.

Do not pretend a COM port is a permanent hardware serial number.

## 8. Safe range validation on session load

Restored frequency/amplitude values are checked against current configured
driver limits. Unsafe/out-of-range values are ignored and warnings are returned.

This protects against:

- old sessions created under different hardware configuration;
- manually edited JSON;
- stale ranges after a driver configuration change.

## 9. Waveform aliases/migration

Session/runtime helpers canonicalize supported carrier waveform names and can
recognize legacy aliases. Unsupported names are ignored with warnings rather
than being sent to hardware.

## 10. Camera profiles

`user_data/camera_profiles.json` stores persistent camera identity/mode metadata,
including:

- device name/ID;
- requested resolution/FPS modes;
- observed/stable FPS history;
- exposure-related practical limits/metadata.

The store writes atomically through a temporary file and replaces the target.

Do not use camera profile data as a reason to skip actual device readback when
performing a safety-relevant control action.

## 11. Analytics cache/results

Analytics stores settings/results under the runtime data paths. The result cache
includes recording/configuration inputs, but current result paths can still
collide on filename stem and partial/cancelled results need explicit status
validation. Do not treat a cache hit as proof of a complete analysis.

Large source videos should normally remain outside source control.

## 12. Diagnostics output

Hardware probes should write under:

```text
user_data/diagnostics/
```

Keep labelled diagnostic captures when they are useful evidence for driver or
firmware research, but do not commit arbitrary machine-specific logs to the
release branch.

Some existing tools still write to legacy project-level locations or emit PNGs
as part of a smoke test. Record the actual path in a diagnostic result and move
new output under `user_data/diagnostics/`.

## 13. Environment variables

Current useful overrides:

| Variable | Meaning |
| --- | --- |
| `FLUIDICSTUDIO_USER_DATA` | alternate generated-data root |
| `DNX64_DLL` | explicit DNX64 DLL path |
| `FLUIDICSTUDIO_CAMERA_PROFILES` | alternate camera profile JSON path |

## 14. What belongs in Git

Commit:

- source code;
- docs;
- small version-controlled configuration;
- deterministic fixtures required by tests;
- vendor files only when redistribution/legal policy permits them.

Do not normally commit:

- real experiment videos;
- sensor CSV runs;
- generated Analytics results;
- `__pycache__`/pytest cache;
- machine-specific diagnostics;
- temporary camera captures.

## 15. If you change a persistent schema

1. add a schema/version field where appropriate;
2. maintain migration for existing files;
3. validate data before applying it to hardware state;
4. preserve safe OFF semantics;
5. write atomically when corruption would be costly;
6. add migration/validation tests;
7. update this document.
