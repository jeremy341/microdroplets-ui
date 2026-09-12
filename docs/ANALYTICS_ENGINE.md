# Analytics Engine

This document describes the offline droplet-analysis backend separately from the
Analytics page UI.

The current implementation analyzes **recorded video**. Still-image geometry is
a planned extension, not current release behavior.

## 1. Package map

```text
backend/analytics/
├─ models.py          data/config/result models
├─ video_source.py    video metadata and frame access
├─ detector.py        frame-level droplet candidate detection/refinement
├─ tracker.py         temporal track association/stitching/measurement
├─ temporal.py        line-crossing/event timing helpers
├─ analyzer.py        complete orchestration pipeline
├─ calibration.py     pixel ↔ physical scale helpers
├─ render.py          annotated frame rendering
├─ csv_export.py      human-readable result export
└─ result_store.py    settings/cache/result persistence
```

## 2. High-level pipeline

```text
video
 ↓
VideoSource metadata/frame access
 ↓
AnalysisConfig
 ↓
auto/manual ROI + background/context
 ↓
DropletDetector per frame
 ↓
DropletTracker across time
 ↓
track stitching / duplicate suppression / geometry stabilization
 ↓
analysis-line/event selection
 ↓
temporal crossing/count assignment
 ↓
DropletMeasurement objects
 ↓
calibration + summary
 ↓
cache / CSV / overlays
```

## 3. Important conceptual distinction: event vs geometry

The project explicitly separates:

```text
counted temporal event
```

from:

```text
valid geometric measurement
```

A droplet can contribute to event timing/counting even when its length/shape
estimate is rejected or repaired differently. Do not collapse those concepts
into one `valid=True` flag.

## 4. VideoSource

`VideoSource` owns OpenCV video access and metadata probing.

It provides:

- frame width/height;
- FPS;
- frame count/duration metadata;
- random frame access;
- frame iteration.

A bad/unopenable file raises `VideoOpenError` rather than letting downstream
code operate on empty frames.

Frame-count metadata is not a complete integrity check. A truncated recording
can end before the advertised frame count and still reach the normal result path;
callers must distinguish complete input from a partial read.

## 5. Analysis configuration

`AnalysisConfig` contains the parameters needed to reproduce an analysis,
including ROI/configuration information. It can be serialized to/from JSON-like
data and participates in the cache key.

A future developer should treat the config as part of the scientific result:
changing detector parameters without changing the cache identity can silently
reuse incompatible results.

## 6. Detector

`DropletDetector` is built from a `DetectorContext` that includes video-derived
background/context information.

Responsibilities include:

- ROI normalization;
- candidate segmentation/contours;
- artifact overlap handling;
- candidate refinement;
- geometry relative to estimated flow direction.

The detector produces per-frame `Detection` objects. It does not itself decide
the final unique droplet count across the recording.

## 7. Flow direction and ROI

`DropletAnalyzer` can estimate flow direction and choose/adjust an analysis ROI.
The analysis is therefore not merely a fixed global contour threshold.

Automatic ROI currently has a persistence/cache coupling: it can update saved
settings while the effective analysis identity does not change in the same way.
Keep runtime-derived ROI separate from user-authored configuration when fixing
this boundary.

When modifying ROI logic, verify both:

- detector geometry;
- temporal line-crossing/count behavior.

## 8. Tracking

`DropletTracker` associates detections through frames and creates `Track`
objects.

The tracker package also contains helpers for:

- prediction;
- track stitching;
- duplicate suppression;
- crossing recomputation;
- robust velocity estimation;
- stable geometry sample selection;
- confidence scoring;
- conversion from track to final measurement.

Tracking changes can affect both count and physical measurements, so they need
real-video regression checks rather than only synthetic unit tests.

## 9. Temporal event detection

`temporal.py` and analyzer logic identify crossings through analysis lines/bands
and reconcile event times.

The purpose is to make generation-rate/event counting robust to situations where
one physical droplet is detected across many frames.

## 10. Geometry repair/stabilization

The analyzer contains logic to choose stable event geometry sources and repair
missing/unreliable event-frame geometry from temporally related measurements.

This is one reason a single frame's contour should not automatically be treated
as the final droplet length.

## 11. Velocity

Velocity is estimated from track motion projected along the flow direction.
The tracker includes robust/linear fitting helpers and confidence logic.

Physical units require a valid calibration.

## 12. Calibration

`backend/analytics/calibration.py` supports:

- scale from a known reference length in pixels;
- direct manual µm-per-pixel scale.

`Calibration` can convert:

```text
pixel length → µm
pixel/s velocity → mm/s
```

Calibration is tied to image resolution compatibility. Do not reuse a scale from
one resolution blindly at another.

## 13. Summary outputs

`AnalysisSummary` aggregates per-droplet measurements and event timing into
recording-level statistics such as counts, mean dimensions, velocities and
generation timing/rate where available.

Exact fields should be read from `backend/analytics/models.py` when extending the
schema, but high-level semantics should remain documented here.

## 14. Overlays and rendering

`render_analysis_frame()` produces annotated visual output from analysis data.
Rendering should remain downstream of detection/tracking; drawing changes must
not change scientific results.

## 15. Result caching

`compute_cache_key(video_path, config)` ties cached output to both recording and
analysis configuration.

`ResultStore` persists:

- Analytics settings;
- per-video results;
- cached result loading.

Runtime paths are documented in [Data and Sessions](DATA_SESSIONS.md).

Current cache limitations:

- cache directories are vulnerable to collisions between videos sharing a stem;
- partial/cancelled results can be loaded like ordinary cache hits;
- the optional expected cache key makes stale-hit handling dependent on callers;
- cancellation can leave a worker/result race that updates the UI after a newer
  analysis has started.

Treat a cache hit as usable only after checking source identity, configuration,
completion status and cancellation state.

## 16. CSV export

`export_result_csv()` creates human-readable Analytics CSV output with stable
formatting and unique destination paths.

The release cleanup intentionally favors readable labels/units over raw internal
object dumps.

## 17. Current benchmark history and next detector work

The current detector line was historically benchmarked around the V65/V66
development phase. Three real recordings were selected as an initial benchmark
set:

```text
recording_20260820_164623(1).mp4
recording_20260820_164740.mp4
recording_20260820_172002.mp4
```

The intended baseline metrics include:

- detected events/droplets;
- valid geometry measurements;
- individual and mean length;
- velocity;
- generation rate;
- rejection reasons.

The future V66-style work should be driven by measured error classes rather than
blind OpenCV parameter tuning. See [Future Roadmap](FUTURE_ROADMAP.md).

The recordings and result manifests are not part of the release repository, so
the historical benchmark is not reproducible from a clean clone and must not be
described as current bench validation without external retained artifacts.

## 18. Planned still-image Analytics

A still photo can support geometry-related analysis when calibration/ROI are
available, but it cannot inherently provide temporal quantities such as:

- velocity;
- generation frequency/rate;
- time-based spacing.

A future image mode should therefore share detector/calibration/geometry code but
produce a different capability/output set instead of pretending a photo is a
one-frame video with meaningful temporal metrics.

## 19. Safe development workflow for detector changes

1. Keep an unchanged baseline result for real recordings.
2. Define the failure class being targeted.
3. Change one pipeline stage intentionally.
4. Compare count, geometry, velocity and rejection reasons separately.
5. Inspect annotated frames/tracks, not just aggregate means.
6. Add a regression test for deterministic logic.
7. Re-run the real-video benchmark before claiming improvement.

## 20. Relevant tests

```text
test_analytics_backend.py
test_analytics_postprocessing.py
test_analytics_event_geometry.py
```

These tests are necessary but do not replace a real-video benchmark.

The UI also needs an empty-result guard: graph metadata such as label/unit must
not be assumed to exist when an analysis returns no plottable series.
